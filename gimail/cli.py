"""JSON-first command line interface. stdout is one result, never debug logs."""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from . import __version__
from .accounts import (
    Account, SECURITIES, SMTP_FIELDS, SMTP_SECURITIES, add_account, config_path, environment_account,
    load_config, remove_account, select_account, update_account,
)
from .drafts import draft
from .errors import GimailError
from .imap_client import MailClient, mailbox
from .smtp_client import send


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        # argparse diagnostics can echo secrets accidentally passed as arguments.
        raise GimailError("Invalid arguments. Use --help on the command for usage.", exit_status=2)


def positive(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def port_value(value):
    number = positive(value)
    if number > 65535:
        raise argparse.ArgumentTypeError("expected a port between 1 and 65535")
    return number


def uid_value(value):
    if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 4294967295:
        raise argparse.ArgumentTypeError("expected one UID between 1 and 4294967295")
    return int(value)


def common(parser, account_config=False):
    # SUPPRESS prevents a subparser's defaults overwriting earlier global flags.
    account_help = ("explicit saved account; must match NAME if supplied" if account_config
                    else "use this saved account (also GIMAIL_ACCOUNT)")
    for names, options in (
        (("--text",), dict(action="store_true", help="human-readable output instead of JSON")),
        (("--config",), dict(metavar="PATH", help="accounts config path (also GIMAIL_CONFIG)")),
        (("--account",), dict(metavar="NAME", help=account_help)),
        (("--folder",), dict(metavar="FOLDER", help="source mailbox (default: INBOX)")),
        (("--confirm",), dict(action="store_true", help="send mail, save drafts, or apply mail mutations/account update/remove instead of previewing")),
    ):
        if account_config and names == ("--folder",):
            continue
        parser.add_argument(*names, default=argparse.SUPPRESS, **options)


def smtp_options(parser):
    parser.add_argument("--smtp-host", help="SMTP hostname; shares IMAP login and credential source")
    parser.add_argument("--smtp-port", type=port_value, help="default on setup: 465 for ssl, 587 for starttls")
    parser.add_argument("--smtp-security", choices=SMTP_SECURITIES, help="default on setup: ssl; update preserves existing port")


def build_parser():
    parser = Parser(prog="gimail", description="Scriptable IMAP and SMTP, JSON by default. No Gmail API.")
    common(parser)
    parser.add_argument("--version", action="version", version="gimail " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)

    account = commands.add_parser("account", help="manage or test accounts")
    common(account)
    accounts = account.add_subparsers(dest="account_command", required=True)
    add = accounts.add_parser("add", help="add an account without overwriting existing names")
    common(add)
    add.add_argument("name")
    add.add_argument("--preset", choices=("gmail",))
    add.add_argument("--host")
    add.add_argument("--port", type=port_value)
    add.add_argument("--user", required=True)
    add.add_argument("--security", choices=SECURITIES, default="ssl", help="default: ssl; plain sends credentials unencrypted")
    credentials = add.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--password-env", metavar="VARIABLE", help="preferred: store only an environment variable name")
    credentials.add_argument("--password-stdin", action="store_true", help="read and store a plaintext password from stdin")
    credentials.add_argument("--keyring", action="store_true", help="retrieve the password from GNOME Keyring via secret-tool; does not create an entry")
    add.add_argument("--default", action="store_true", help="make this account the default")
    smtp_options(add)

    update = accounts.add_parser("update", help="preview changing a saved account; apply with --confirm")
    common(update, account_config=True)
    update.add_argument("name")
    update.add_argument("--host")
    update.add_argument("--port", type=port_value)
    update.add_argument("--user")
    update.add_argument("--security", choices=SECURITIES, help="change security only; use --port to change the port too")
    updated_credentials = update.add_mutually_exclusive_group()
    updated_credentials.add_argument("--password-env", metavar="VARIABLE", help="replace credential source with an environment variable name, not its value")
    updated_credentials.add_argument("--password-stdin", action="store_true", help="read one plaintext password line from stdin; store only with --confirm")
    updated_credentials.add_argument("--keyring", action="store_true", help="use GNOME Keyring via secret-tool; does not create, change, or delete keyring entries")
    update.add_argument("--default", action="store_true", help="make this account the default")
    smtp_options(update)

    remove = accounts.add_parser("remove", help="preview removing a saved profile; choose from a numbered list when NAME is omitted")
    common(remove, account_config=True)
    remove.add_argument("name", nargs="?", help="saved account name, or omit for the interactive picker")

    common(accounts.add_parser("list", help="list saved accounts and environment setup, without secrets"))
    test = accounts.add_parser("test", help="test login and read-only access to the selected folder")
    common(test)
    test.add_argument("name", nargs="?", help="saved account name (or use --account)")

    listing = commands.add_parser("list", help="list message headers, newest UID first")
    common(listing)
    listing.add_argument("--unread", action="store_true")
    listing.add_argument("--limit", type=positive, default=20)

    show = commands.add_parser("show", help="show a message without marking it read")
    common(show)
    show.add_argument("uid", type=uid_value)

    search = commands.add_parser("search", help="search messages; filters are ANDed")
    common(search)
    search.add_argument("--from", dest="sender")
    search.add_argument("--subject")
    search.add_argument("--query", help="raw, single-line IMAP search criteria")
    search.add_argument("--limit", type=positive, default=20)

    mark = commands.add_parser("mark", help="preview changing Seen; apply with --confirm")
    common(mark)
    mark.add_argument("uid", type=uid_value)
    flags = mark.add_mutually_exclusive_group(required=True)
    flags.add_argument("--read", dest="read", action="store_true")
    flags.add_argument("--unread", dest="read", action="store_false")

    move = commands.add_parser("move", help="preview moving one UID; apply with --confirm")
    common(move)
    move.add_argument("uid", type=uid_value)
    move.add_argument("destination", metavar="FOLDER")

    delete = commands.add_parser("delete", help="preview setting Deleted (no expunge); apply with --confirm")
    common(delete)
    delete.add_argument("uid", type=uid_value)

    for command, help_text in (("send", "preview sending plain-text mail; submit with --confirm"),
                               ("draft", "preview a plain-text draft; save via IMAP with --confirm (never sends)")):
        composing = commands.add_parser(command, help=help_text)
        common(composing, account_config=True)
        if command == "draft":
            composing.add_argument("--folder", default=argparse.SUPPRESS, metavar="FOLDER",
                                   help="destination mailbox (default: discover special-use Drafts folder)")
        composing.add_argument("--from", dest="sender", help="bare ASCII sender address (default: account user)")
        composing.add_argument("--to", action="append", required=True, help="one bare ASCII recipient; repeat for multiple recipients")
        composing.add_argument("--subject", required=True)
        body = composing.add_mutually_exclusive_group(required=True)
        body.add_argument("--body-file", metavar="PATH", help="read plain-text UTF-8 body from a file")
        body.add_argument("--body-stdin", action="store_true", help="read plain-text UTF-8 body from stdin (all input)")
    return parser


def choose_account_to_remove(path):
    accounts, default = load_config(path)
    if not accounts:
        raise GimailError("No saved accounts to remove.", "not_found")
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise GimailError("An account name is required without an interactive terminal. Use account remove NAME, or --account NAME.", exit_status=2)
    default = default or accounts[0].name
    print("Saved accounts (profiles only; mail is not removed):", file=sys.stderr)
    for index, account in enumerate(accounts, 1):
        label = account.name + (" (default)" if account.name == default else "")
        print(f"  {index}) {terminal_safe(label)}", file=sys.stderr)
    print("Choose index (Enter, 0, or q cancels): ", end="", file=sys.stderr, flush=True)
    choice = sys.stdin.readline().strip()
    if choice.lower() in ("", "0", "q"):
        return None
    if not choice.isascii() or not choice.isdigit() or len(choice) > 10 or not 1 <= int(choice) <= len(accounts):
        raise GimailError("Invalid account index. Nothing was removed; run account remove again.", exit_status=2)
    # Return the displayed object, not just an index into a later config load.
    return accounts[int(choice) - 1]


def dispatch(args):
    path = config_path(args.config)
    if args.command == "account":
        if args.account_command == "add":
            if not args.host and args.preset != "gmail":
                raise GimailError("Provide --host for generic IMAP, or --preset gmail.", exit_status=2)
            record = {
                "name": args.name, "host": args.host or "imap.gmail.com", "user": args.user,
                "port": args.port if args.port is not None else (993 if args.security == "ssl" else 143),
                "security": args.security,
                "smtp_host": args.smtp_host if args.smtp_host is not None else ("smtp.gmail.com" if args.preset == "gmail" else None),
                "smtp_port": args.smtp_port, "smtp_security": args.smtp_security,
            }
            if args.keyring:
                record["password_keyring"] = True
            elif args.password_stdin:
                record["password"] = sys.stdin.readline().rstrip("\r\n")
            else:
                record["password_env"] = args.password_env
            return add_account(path, Account.from_record(record), args.default)
        if args.account_command == "list":
            saved, default = load_config(path)
            env = environment_account()
            return {
                "accounts": [account.public() for account in saved],
                "default_account": default or (saved[0].name if saved else None),
                "environment_account": env.public() if env else None,
            }
        if args.name is not None and args.account is not None and args.name != args.account:
            raise GimailError("Use either the positional account name or a matching --account.", exit_status=2)
        args.account = args.name if args.name is not None else args.account
        if args.account_command == "update":
            changes = {key: getattr(args, key) for key in ("host", "port", "user", "security", "password_env", *SMTP_FIELDS)
                       if getattr(args, key) is not None}
            if args.keyring:
                changes["password_keyring"] = True
            elif args.password_stdin:
                changes["password"] = sys.stdin.readline().rstrip("\r\n")
            return update_account(path, args.account, changes, make_default=args.default, confirm=args.confirm)
        if args.account_command == "remove":
            selected = None
            if args.account is None:
                selected = choose_account_to_remove(path)
                if selected is None:
                    return {"action": "account_remove", "cancelled": True, "dry_run": True}
                args.account = selected.name
            return remove_account(path, args.account, confirm=args.confirm, expected_account=selected)

    if args.command in ("send", "draft"):
        account = select_account(path, args.account)
        try:
            if args.body_stdin:
                # Decode explicitly rather than depending on the caller's locale.
                body = sys.stdin.buffer.read().decode("utf-8") if hasattr(sys.stdin, "buffer") else sys.stdin.read()
            else:
                body = Path(args.body_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise GimailError("Cannot read body: expected an accessible UTF-8 file or stdin.",
                              "smtp_error" if args.command == "send" else "imap_error", 2) from None
        if args.command == "draft":
            return draft(account, args.sender, args.to, args.subject, body, folder=args.folder, confirm=args.confirm)
        return send(account, args.sender, args.to, args.subject, body, confirm=args.confirm)

    mailbox(args.folder)  # Validate before opening a connection.
    if args.command == "move":
        mailbox(args.destination)
    account = select_account(path, args.account)
    mutation = args.command in ("mark", "move", "delete")
    with MailClient(account) as client:
        client.select(args.folder, readonly=not (mutation and args.confirm))
        if args.command == "account":
            return {**client.context(), "connected": True, "security": account.security}
        if args.command == "list":
            return client.search(unread=args.unread, limit=args.limit)
        if args.command == "search":
            return client.search(sender=args.sender, subject=args.subject, query=args.query, limit=args.limit)
        if args.command == "show":
            return {**client.context(), **client.fetch(args.uid, include_body=True)}
        return client.mutate(args.command, args.uid, confirm=args.confirm,
                             read=getattr(args, "read", None), destination=getattr(args, "destination", None))


def terminal_safe(value, multiline=False):
    result = []
    for char in str(value):
        if char == "\n" and multiline:
            result.append(char)
        elif unicodedata.category(char).startswith("C"):
            result.append(" " if char in "\r\n\t" else "\\u{:04x}".format(ord(char)))
        else:
            result.append(char)
    return "".join(result)


def render_text(data):
    if data.get("action") == "draft":
        prefix = "DRY RUN" if data["dry_run"] else ("SAVED" if data["saved"] else "UNKNOWN" if data["saved"] is None else "NOT SAVED")
        lines = [f"{prefix}: draft via {data['account']}",
                 "Folder: " + (data["folder"] or "(discover Drafts when saving)"),
                 "From: " + data["from"], "To: " + ", ".join(data["to"]),
                 "Subject: " + data["subject"], "Message-ID: " + data["message_id"]]
        lines.extend(data[key] for key in ("note", "hint") if key in data)
        if "body" in data:
            lines.extend(("", data["body"]))
        return terminal_safe("\n".join(lines), multiline=True)
    if data.get("action") == "send":
        prefix = "DRY RUN" if data["dry_run"] else data["delivery"].upper()
        lines = [f"{prefix}: send via {data['account']}",
                 f"SMTP: {data['smtp_host']}:{data['smtp_port']} ({data['smtp_security']})", f"From: {data['from']}",
                 "To: " + ", ".join(data["to"]), "Subject: " + data["subject"],
                 "Message-ID: " + data["message_id"]]
        if "accepted" in data:
            lines.extend(("Accepted: " + ", ".join(data["accepted"]), "Refused: " + ", ".join(data["refused"])))
        lines.extend(data[key] for key in ("note", "hint") if key in data)
        if "body" in data:
            lines.extend(("", data["body"]))
        return terminal_safe("\n".join(lines), multiline=True)
    if data.get("action") in ("account_update", "account_remove"):
        if data.get("cancelled"):
            return "Cancelled. No config changes."
        prefix = "DRY RUN" if data["dry_run"] else "APPLIED"
        if not data["dry_run"] and data.get("changed_fields") == []:
            prefix = "NO CHANGES"
        action = data["action"].split("_", 1)[1]
        lines = [terminal_safe(f"{prefix}: {action} account {data['account']}")]
        if "changed_fields" in data:
            lines.append("Changed fields: " + (", ".join(data["changed_fields"]) or "none"))
        lines.append(terminal_safe("Default account: " + (data["default_account"] or "(none)")))
        if "remaining_accounts" in data:
            lines.append(f"Remaining saved accounts: {data['remaining_accounts']}")
        lines.extend(terminal_safe(data[key]) for key in ("note", "hint") if key in data)
        return "\n".join(lines)
    if "dry_run" in data:
        prefix = "DRY RUN" if data["dry_run"] else "APPLIED"
        details = f"{data['action']} UID {data['uid']} in {data['folder']}"
        if "destination" in data:
            details += " -> " + data["destination"]
        if "read" in data:
            details += " (read)" if data["read"] else " (unread)"
        lines = [terminal_safe(prefix + ": " + details), terminal_safe(data["subject"])]
        lines.extend(terminal_safe(data[key]) for key in ("note", "hint") if key in data)
        return "\n".join(lines)
    if "messages" in data:
        lines = ["UID\tSTATE\tFROM\tSUBJECT\tDATE"]
        for message in data["messages"]:
            lines.append("\t".join(terminal_safe(value) for value in (
                message["uid"], "unread" if message["unread"] else "read",
                message["from"], message["subject"], message["date"],
            )))
        if not data["messages"]:
            lines.append("(no messages)")
        return "\n".join(lines)
    if "text" in data:
        lines = [terminal_safe(f"{key.title()}: {data[key]}") for key in ("uid", "from", "to", "subject", "date")]
        body = data["text"] or data["html"]
        lines.extend(("", terminal_safe(body, multiline=True)))
        if data["attachments"]:
            lines.append("\nAttachments:")
            lines.extend(terminal_safe(f"- {part['filename'] or '(unnamed)'} ({part['content_type']}, {part['size']} bytes)") for part in data["attachments"])
        return "\n".join(lines)
    return terminal_safe(json.dumps(data, indent=2, ensure_ascii=True), multiline=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    text = "--text" in argv
    status = 0
    args = None
    try:
        args = build_parser().parse_args(argv)
        for key, default in (("text", False), ("config", None), ("account", None), ("folder", None if args.command == "draft" else "INBOX"), ("confirm", False)):
            if not hasattr(args, key):
                setattr(args, key, default)
        text = args.text
        result = {"ok": True, "data": dispatch(args)}
    except GimailError as exc:
        result = {"ok": False, "error": str(exc), "code": exc.code}
        if exc.data is not None:
            result["data"] = exc.data
        status = exc.exit_status
    except KeyboardInterrupt:
        result = {"ok": False, "error": "Interrupted. If confirming a change, inspect server state before retrying.",
                  "code": "smtp_error" if args is not None and args.command == "send" else "imap_error"}
        status = 130
    except Exception:
        # Do not expose tracebacks, server responses, config content, or credentials.
        result = {"ok": False, "error": "Unexpected failure; details suppressed to protect credentials. If confirming a change, inspect server state before retrying.",
                  "code": "smtp_error" if args is not None and args.command == "send" else "imap_error"}
        status = 1
    if text:
        output = render_text(result["data"]) if result["ok"] else f"Error ({result['code']}): {result['error']}"
        if not result["ok"] and "data" in result:
            output += "\n" + render_text(result["data"])
    else:
        output = json.dumps(result, ensure_ascii=True)
    try:
        print(output, flush=True)
    except BrokenPipeError:
        # Avoid a second BrokenPipeError when the interpreter flushes stdout.
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        return 0
    return status
