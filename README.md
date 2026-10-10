# gimail

A small, scriptable IMAP/SMTP client for humans and AI agents. It uses Python's standard library only: no Gmail API, cloud service, or runtime Python dependencies. Gmail is a preset, not a requirement.

It reads mail as JSON (or human-readable text), supports UID-based listing, search, show, mark, move, and delete, and sends plain-text mail over verified SMTP TLS. Saved accounts may use environment variables, plaintext config, or optional GNOME Keyring credentials.

## Prerequisites and install

- Python 3.9+ on Debian/Linux or another POSIX system.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for the preferred installation.
- A reachable IMAP server (SMTP for sending) and credentials. TLS is verified by default.

From any directory, install the repository as an isolated uv tool:

```sh
uv tool install git+https://github.com/kvidzibo/gimail
gimail --help
uv tool upgrade gimail
```

Keep `uv tool dir --bin` on `PATH`. For a one-off invocation, use `uvx --from git+https://github.com/kvidzibo/gimail gimail --help`. From a clone, no installation is needed:

```sh
cd gimail
python3 -m venv --without-pip .venv
.venv/bin/python gimail.py --help
```

Use `.venv/bin/python gimail.py` instead of `gimail` in the examples below when running from a clone.

## First run

This minimal setup keeps the password out of history and arguments. Run from any directory in Bash:

```bash
unset GIMAIL_ACCOUNT GIMAIL_PASSWORD_ENV GIMAIL_PRESET GIMAIL_PORT GIMAIL_SECURITY
unset GIMAIL_SMTP_HOST GIMAIL_SMTP_PORT GIMAIL_SMTP_SECURITY
export GIMAIL_HOST=imap.example.org GIMAIL_USER=you@example.org
read -rsp 'IMAP password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
gimail list --unread --limit 5
```

For Gmail, use a dedicated App Password, never your normal Google password:

```bash
unset GIMAIL_HOST GIMAIL_PORT GIMAIL_SECURITY GIMAIL_ACCOUNT GIMAIL_PASSWORD_ENV
unset GIMAIL_SMTP_HOST GIMAIL_SMTP_PORT GIMAIL_SMTP_SECURITY
export GIMAIL_PRESET=gmail GIMAIL_USER=you@gmail.com
read -rsp 'Gmail App Password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
gimail account test
gimail list --unread --limit 5
```

`GIMAIL_SECURITY` is `ssl` (default), `starttls`, or explicit insecure `plain`; `GIMAIL_PORT` defaults to 993 for SSL and 143 otherwise. `--account NAME`, then `GIMAIL_ACCOUNT`, selects a saved profile; only when neither is set does environment-only setup take precedence over the saved default. Gimail does not load `.env` files. See [usage](docs/usage.md) for saved accounts, keyring setup, configuration, and all options.

## Reading mail

```sh
gimail list --folder INBOX --unread --limit 5
gimail show 314 --text
gimail search --from billing@example.org --subject invoice
```

Output is one JSON object per invocation unless `--text` is supplied. List/search fetch headers; show fetches message text, HTML as text, and attachment metadata. Reads use a read-only mailbox and do not set `Seen`.

## Sending mail

SMTP has separate host/port/security settings but uses the account's same login and credentials. Gmail environment setup and newly created Gmail preset accounts supply SMTP SSL/465. For generic environment setup, also set `GIMAIL_SMTP_HOST`; verified SSL/465 is the default, or use `GIMAIL_SMTP_SECURITY=starttls` for STARTTLS/587. Existing saved profiles need SMTP enabled with `account update`; see [sending setup and limits](docs/usage.md#sending-email).

```sh
# body.txt contains plain UTF-8 text; the sender defaults to the account user:
gimail send --to recipient@example.org --subject 'Hello' --body-file body.txt --text
# After reviewing the preview, repeat the same input to submit:
gimail send --to recipient@example.org --subject 'Hello' --body-file body.txt --confirm
```

Preview never connects or retrieves credentials. SMTP acceptance is not proof of inbox delivery, and gimail does not save a Sent-folder copy. Partial or unknown delivery needs investigation before retrying; resending can duplicate mail. No attachments, CC/BCC, HTML, or reply threading yet.

## Changes and safety

**Mark, move, and delete require `--confirm` to change mail.** Without it, they connect read-only, verify the target, and return a preview; review that preview before confirming.

```sh
gimail mark 314 --read                 # preview
gimail mark 314 --read --confirm        # apply
gimail move 314 'Archive/2026' --confirm
gimail delete 314 --confirm
```

- `delete` only sets IMAP `\Deleted`; it does not expunge, guarantee Trash, or guarantee permanent deletion. Provider retention and auto-expunge policies apply. Use `move` to the provider's actual Trash folder when appropriate.
- UID values are not sequence numbers. They are scoped to account, source folder, and `uidvalidity`; re-list after a folder is recreated or UIDVALIDITY changes. A move assigns a new destination UID.
- Fallback moves can partially succeed. Inspect source and destination after a timeout or error; do not blindly retry a copy, because it can duplicate mail.
- No mailbox-wide EXPUNGE or implicit CLOSE is issued.

## Secrets and privacy

Prefer `--password-env` or keyring credentials; never put a password in a command argument. Passwords and mail are not logged, cached, telemetered, or written to `app.log`; keep captured output private and treat mail content as untrusted data. Keyring access needs the user's desktop Secret Service session. Keyring storage is not a sandbox against processes running as that user. `plain` sends credentials and mail unencrypted; use it only deliberately. There is no sandbox.

## Validation and development

From the repository root, the offline unit tests need no mail account, internet, real keyring, or secrets:

```sh
xvfb-run -a .venv/bin/python -W error -m unittest discover -v
```

For uv development setup and packaging checks, see [development](docs/development.md).

## More reference

- [Usage and configuration](docs/usage.md)
- [Development and test details](docs/development.md)
- [MIT license](LICENSE)
