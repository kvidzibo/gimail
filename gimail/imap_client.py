"""UID-only IMAP operations; no global EXPUNGE or implicit mark-as-read."""
from __future__ import annotations

import base64
import imaplib
import re
import ssl
from email import policy
from email.parser import BytesParser

from .accounts import clean_string
from .errors import GimailError


UID_RE = re.compile(rb"\bUID\s+(\d+)\b", re.I)
SIZE_RE = re.compile(rb"\bRFC822\.SIZE\s+(\d+)\b", re.I)
NETWORK_ERRORS = (imaplib.IMAP4.error, OSError, EOFError, UnicodeError, ValueError)


def quote(value):
    if not clean_string(value):
        raise GimailError("IMAP arguments must be nonempty and cannot contain control characters.", exit_status=2)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mailbox(value):
    """Encode an IMAP4rev1 mailbox using modified UTF-7, then quote it."""
    quote(value)  # Validate before encoding (including newline injection).
    result, pending = [], []

    def flush():
        if pending:
            encoded = base64.b64encode("".join(pending).encode("utf-16-be")).decode("ascii")
            result.append("&" + encoded.rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for char in value:
        if 32 <= ord(char) <= 126:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return quote("".join(result))


def fetch_responses(data):
    """Reassemble metadata split by literals, keeping unsolicited FETCHes separate."""
    headers, literals = [], []
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            header, literal = item
        else:
            header, literal = item, None
        if not isinstance(header, bytes):
            continue
        if headers and re.match(rb"^\d+\s+\(", header):
            yield b" ".join(headers), literals
            headers, literals = [], []
        headers.append(header)
        if isinstance(literal, bytes):
            literals.append(literal)
    if headers:
        yield b" ".join(headers), literals


def decoded_payload(part):
    content = part.get_payload(decode=True)
    if content is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    try:
        return content.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def message_data(uid, header, content, include_body=False):
    msg = BytesParser(policy=policy.default).parsebytes(content)
    flags = [flag.decode("ascii", "replace") for flag in imaplib.ParseFlags(header)]
    size_match = SIZE_RE.search(header)
    result = {
        "uid": uid,
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "cc": str(msg.get("Cc", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-ID", "")),
        "flags": flags,
        "unread": not any(flag.lower() == r"\seen" for flag in flags),
        "size": int(size_match.group(1)) if size_match else len(content),
    }
    if include_body:
        texts, htmls, attachments = [], [], []

        def visit(part):
            filename = part.get_filename()
            if part.get_content_disposition() == "attachment" or filename is not None:
                payload = part.get_payload(decode=True)
                attachments.append({
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "size": len(payload) if payload is not None else len(part.as_bytes()),
                })
            elif part.is_multipart():
                for child in part.iter_parts():
                    visit(child)
            elif part.get_content_type() == "text/plain":
                texts.append(decoded_payload(part))
            elif part.get_content_type() == "text/html":
                htmls.append(decoded_payload(part))

        visit(msg)
        result.update(text="\n".join(texts), html="\n".join(htmls), attachments=attachments)
    return result


class MailClient:
    def __init__(self, account):
        self.account = account
        self.connection = None
        self.capabilities = set()
        self.folder = None
        self.uidvalidity = None

    def __enter__(self):
        password = self.account.secret()
        try:
            if self.account.security == "ssl":
                self.connection = imaplib.IMAP4_SSL(
                    self.account.host, self.account.port,
                    ssl_context=ssl.create_default_context(), timeout=30,
                )
            else:
                self.connection = imaplib.IMAP4(self.account.host, self.account.port, timeout=30)
                self.connection.debug = 0
                if self.account.security == "starttls":
                    self._call(self.connection.starttls, ssl_context=ssl.create_default_context(),
                               message="STARTTLS failed; no credentials were sent.")
            self.connection.debug = 0
            try:
                # Old imaplib versions do not quote the LOGIN username themselves.
                if self.account.user.isascii() and password.isascii():
                    self._call(self.connection.login, quote(self.account.user), password,
                               message="Authentication failed. Check username and password (Gmail requires an App Password).",
                               code="auth_failed")
                else:
                    # SASL PLAIN supports UTF-8 credentials and avoids LOGIN's ASCII encoding.
                    self._call(self.connection.authenticate, "PLAIN",
                               lambda _: b"\0" + self.account.user.encode("utf-8") + b"\0" + password.encode("utf-8"),
                               message="Authentication failed; non-ASCII credentials require AUTH=PLAIN support.",
                               code="auth_failed")
            finally:
                password = None
            data = self._call(self.connection.capability, message="Could not read server capabilities.")
            self.capabilities = {
                token.decode("ascii", "replace").upper()
                for line in data if isinstance(line, bytes) for token in line.split()
            }
            return self
        except GimailError:
            self.disconnect()
            raise
        except NETWORK_ERRORS:
            self.disconnect()
            raise GimailError("Could not connect to the IMAP server. Check host, port, security, and TLS certificates.") from None

    def __exit__(self, *_):
        self.disconnect()

    def disconnect(self):
        if self.connection is not None:
            try:
                # CLOSE would expunge unrelated messages. LOGOUT does not.
                self.connection.logout()
            except Exception:
                try:
                    self.connection.shutdown()
                except Exception:
                    pass
            finally:
                self.connection = None

    @staticmethod
    def _call(function, *args, message="IMAP operation failed.", code="imap_error", **kwargs):
        try:
            status, data = function(*args, **kwargs)
        except NETWORK_ERRORS:
            raise GimailError(message, code) from None
        if status != "OK":
            raise GimailError(message, code)
        return data

    def select(self, folder="INBOX", readonly=True):
        self._call(self.connection.select, mailbox(folder), readonly=readonly,
                   message="Folder not found or not accessible.", code="not_found")
        self.folder = folder
        _, data = self.connection.response("UIDVALIDITY")
        if data and isinstance(data[0], bytes) and data[0].isdigit():
            self.uidvalidity = int(data[0])
        return self.context()

    def context(self):
        return {"account": self.account.name, "folder": self.folder, "uidvalidity": self.uidvalidity}

    def search(self, unread=False, sender=None, subject=None, query=None, limit=20):
        criteria = []
        if unread:
            criteria.append("UNSEEN")
        if sender is not None:
            criteria.extend(("FROM", quote(sender)))
        if subject is not None:
            criteria.extend(("SUBJECT", quote(subject)))
        if query is not None:
            quote(query)  # Validate, but deliberately keep the raw IMAP search grammar.
            criteria.append(query)
        if not criteria:
            criteria.append("ALL")
        charset = []
        if any(not item.isascii() for item in criteria):
            charset = ["CHARSET", "UTF-8"]
        data = self._call(self.connection.uid, "SEARCH", *charset,
                          *(item.encode("utf-8") for item in criteria),
                          message="IMAP search failed. Check the query and the server's search/charset support.")
        try:
            if not isinstance(data, (list, tuple)) or any(line is not None and not isinstance(line, bytes) for line in data):
                raise ValueError
            tokens = b" ".join(line for line in data if isinstance(line, bytes)).split()
            if any(not token.isdigit() or not 0 < int(token) <= 4294967295 for token in tokens):
                raise ValueError
            uids = sorted({int(token) for token in tokens}, reverse=True)[:limit]
        except ValueError:
            raise GimailError("Server returned invalid search UIDs.") from None
        messages = []
        for uid in uids:
            try:
                messages.append(self.fetch(uid))
            except GimailError as exc:
                if exc.code != "not_found":
                    raise
                # A message may disappear between SEARCH and FETCH.
        return {**self.context(), "count": len(messages), "messages": messages}

    def fetch(self, uid, include_body=False):
        fields = "BODY.PEEK[]" if include_body else "BODY.PEEK[HEADER.FIELDS (DATE FROM TO CC SUBJECT MESSAGE-ID)]"
        data = self._call(self.connection.uid, "FETCH", str(uid), f"(UID FLAGS RFC822.SIZE {fields})",
                          message="Could not fetch message.")
        for header, literals in fetch_responses(data):
            match = UID_RE.search(header)
            if match and int(match.group(1)) == uid and literals:
                return message_data(uid, header, literals[0], include_body)
        raise GimailError("Message UID not found in this folder.", "not_found")

    def _store(self, uid, operation, flag, message):
        data = self._call(self.connection.uid, "STORE", str(uid), operation, "(" + flag + ")", message=message)
        for header, _ in fetch_responses(data):
            match = UID_RE.search(header)
            if match and int(match.group(1)) == uid:
                return
        raise GimailError(message + " Server did not acknowledge this UID; the message may have disappeared.")

    def mutate(self, action, uid, confirm=False, read=None, destination=None):
        if action == "move":
            mailbox(destination)  # Reject malformed targets even during preview.
            if destination == self.folder or (destination.upper() == self.folder.upper() == "INBOX"):
                raise GimailError("Source and destination folders must differ.", exit_status=2)
        message = self.fetch(uid)
        result = {**self.context(), "action": action, "uid": uid, "subject": message["subject"], "dry_run": not confirm}
        if action == "mark":
            result["read"] = read
        elif action == "move":
            result["destination"] = destination
            result["method"] = "MOVE" if "MOVE" in self.capabilities else "COPY+STORE"
            if result["method"] != "MOVE" and "UIDPLUS" not in self.capabilities:
                result["note"] = "Source will be marked Deleted but not expunged; server lacks MOVE and UIDPLUS."
        elif action == "delete":
            result["note"] = "Marks Deleted only; does not expunge. Other clients or server policies may later purge it."
        if not confirm:
            result["hint"] = "Re-run with --confirm to apply."
            return result
        if action == "mark":
            self._store(uid, "+FLAGS" if read else "-FLAGS", r"\Seen",
                        "Mark did not complete cleanly. Inspect message flags before retrying.")
        elif action == "delete":
            self._store(uid, "+FLAGS", r"\Deleted",
                        "Delete did not complete cleanly. Inspect message flags before retrying.")
            result["expunge_requested"] = False
        elif action == "move":
            if "MOVE" in self.capabilities:
                self._call(self.connection.uid, "MOVE", str(uid), mailbox(destination),
                           message="Move did not complete cleanly. Check the destination exists and inspect both folders before retrying.")
                result["expunge_requested"] = True
            else:
                self._call(self.connection.uid, "COPY", str(uid), mailbox(destination),
                           message="Copy did not complete cleanly. Check the destination exists and inspect both folders before retrying.")
                self._store(uid, "+FLAGS", r"\Deleted",
                            "Message was copied, but marking the source Deleted failed. Inspect both folders; do not blindly retry.")
                result["expunge_requested"] = False
                if "UIDPLUS" in self.capabilities:
                    self._call(self.connection.uid, "EXPUNGE", str(uid),
                               message="Message was copied and source marked Deleted, but UID EXPUNGE failed. Inspect both folders before retrying.")
                    result["expunge_requested"] = True
        return result
