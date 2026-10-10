"""Save plain-text drafts via IMAP, never SMTP; previews are entirely offline."""
from __future__ import annotations

import base64
import re

from .errors import GimailError
from .imap_client import MailClient, NETWORK_ERRORS, mailbox
from .smtp_client import compose


QUOTED = rb'"(?:[^"\\\r\n]|\\["\\])*"'
LIST_ROW = re.compile(rb'\(([^)]*)\) +(?:NIL|' + QUOTED + rb') +(.+)', re.IGNORECASE)


def drafts_folder(client):
    """Find exactly one selectable special-use Drafts mailbox, including literals."""
    rows = client._call(client.connection.list, '""', '"*"', message="Could not discover Drafts; use --folder with the exact mailbox path.")
    folders = set()
    for row in rows:
        header, literal = row if isinstance(row, tuple) and len(row) == 2 else (row, None)
        if not isinstance(header, bytes):
            continue
        match = LIST_ROW.fullmatch(header)
        if not match:
            continue
        flags = match[1].lower().split()
        if b'\\drafts' not in flags or b'\\noselect' in flags:
            continue
        try:
            name = match[2]
            if literal is not None:
                if not re.fullmatch(rb'\{[0-9]+\}', name) or not isinstance(literal, bytes):
                    raise ValueError
                if int(name[1:-1]) != len(literal):
                    raise ValueError
                name = literal
            elif re.fullmatch(QUOTED, name):
                name = re.sub(rb'\\(["\\])', rb'\1', name[1:-1])
            elif any(char in name for char in b' (){%*"\\]'):
                raise ValueError
            # IMAP4rev1 LIST names use modified UTF-7, even for localized Gmail folders.
            encoded = name.decode('ascii')
            parts, position = [], 0
            for segment in re.finditer(r'&([^-]*)-', encoded):
                parts.append(encoded[position:segment.start()])
                token = segment[1]
                parts.append(base64.b64decode(token.replace(',', '/') + '=' * (-len(token) % 4), validate=True).decode('utf-16-be') if token else '&')
                position = segment.end()
            parts.append(encoded[position:])
            folder = ''.join(parts)
            # Reject malformed encodings and unsafe names rather than guessing a target.
            if mailbox(folder).encode('ascii')[1:-1] != re.sub(rb'(["\\])', rb'\\\1', name):
                raise ValueError
            folders.add(folder)
        except (ValueError, UnicodeError, GimailError):
            raise GimailError("Invalid Drafts mailbox metadata; use --folder with the exact mailbox path.") from None
    if len(folders) != 1:
        raise GimailError("Could not identify one Drafts mailbox; use --folder with the exact mailbox path.", "not_found")
    return folders.pop()


def draft(account, sender, recipients, subject, body, folder=None, confirm=False):
    if folder is not None:
        mailbox(folder)  # Validate even during preview, before credentials or network access.
    try:
        message, sender, recipients = compose(account.user if sender is None else sender, recipients, subject, body)
    except GimailError as exc:
        raise GimailError(str(exc), "imap_error", exc.exit_status) from None
    result = {
        "action": "draft", "account": account.name, "dry_run": not confirm,
        "folder": folder, "from": sender, "to": recipients, "subject": subject,
        "message_id": str(message["Message-ID"]), "saved": False,
    }
    if not confirm:
        return {**result, "body": body, "hint": "Review the message, then re-run with the same input and --confirm to save, not send. Preview does not connect; Drafts is discovered when saving unless --folder is given."}
    content = message.as_bytes()
    with MailClient(account) as client:
        result["folder"] = folder if folder is not None else drafts_folder(client)
        target = mailbox(result["folder"])
        try:
            status, _ = client.connection.append(target, r'(\Draft)', None, content)
        except (KeyboardInterrupt, *NETWORK_ERRORS) as exc:
            raise GimailError("Draft save status is unknown. Inspect Drafts before retrying; another save can duplicate the message.",
                              "draft_unknown", 130 if isinstance(exc, KeyboardInterrupt) else 1,
                              data={**result, "saved": None}) from None
        if status != "OK":
            raise GimailError("Server refused the draft. Check the folder exists and permits saving.", data=result)
        result.update(saved=True, note="Saved as a draft, not sent. Review, edit, and send in your mail client.")
    return result
