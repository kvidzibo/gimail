# gimail

A small, scriptable IMAP client for humans and AI agents. **Python standard library only; no pip dependencies, Gmail API, or cloud service.** Gmail is a preset, not a requirement.

- JSON by default; `--text` for humans.
- SSL with certificate verification by default; STARTTLS and explicit plaintext IMAP supported.
- UID-based list, show, search, mark, move, and delete.
- Changes to mail are previews until you pass `--confirm`.

## Install

Python **3.9+** on Debian/Linux or another POSIX system. No installation or virtual environment is needed to run:

```sh
git clone https://github.com/kvidzibo/gimail.git
cd gimail
# While the initial implementation PR is open:
git switch --track origin/feat/imap-cli
python3 gimail.py --help
```

`python3 -m gimail` also works from the clone. Optionally make `gimail` available on your PATH (keep the clone in place):

```sh
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/gimail.py" "$HOME/.local/bin/gimail"
export PATH="$HOME/.local/bin:$PATH"
gimail --help
```

The examples below use that optional `gimail` command. Without the symlink, substitute `python3 gimail.py`.

## First run: environment variables only

No accounts file is needed. For a generic IMAP server, in **Bash**:

```bash
export GIMAIL_HOST=imap.example.org
export GIMAIL_USER=you@example.org
read -rsp 'IMAP password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
python3 gimail.py list --unread --limit 5
```

Reading the password this way keeps it out of shell history and command-line arguments. A secret manager can supply the same environment variable. `gimail` does not load `.env` files automatically.

| Variable | Meaning |
| --- | --- |
| `GIMAIL_HOST` | IMAP hostname; activates environment-only account `env` |
| `GIMAIL_PRESET` | `gmail` supplies `imap.gmail.com`; also activates `env` |
| `GIMAIL_USER` | Login username, usually a full email address |
| `GIMAIL_PASSWORD` | Password for the environment-only account |
| `GIMAIL_PASSWORD_ENV` | Optional alternative variable **name** containing that password |
| `GIMAIL_SECURITY` | `ssl` (default), `starttls`, or `plain` |
| `GIMAIL_PORT` | Default `993` for SSL; `143` otherwise |
| `GIMAIL_ACCOUNT` | Select a saved account instead of environment-only setup |
| `GIMAIL_CONFIG` | Override the accounts file path |

`--account NAME` wins over `GIMAIL_ACCOUNT`. Either explicitly selects a **saved** account. Otherwise, an environment-only account wins over the saved default. Without environment setup, `default_account` or the first saved account is used.

### Gmail App Password

1. Turn on Google [2-Step Verification](https://support.google.com/accounts/answer/185839).
2. Create a dedicated password at [Google App Passwords](https://myaccount.google.com/apppasswords). Use that password, **not your normal Google password**. Enter its 16 characters without the display spaces.
3. Use Gmail's preset:

```bash
unset GIMAIL_HOST GIMAIL_PORT GIMAIL_SECURITY GIMAIL_ACCOUNT GIMAIL_PASSWORD_ENV
export GIMAIL_PRESET=gmail
export GIMAIL_USER=you@gmail.com
read -rsp 'Gmail App Password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
python3 gimail.py account test
python3 gimail.py list --unread --limit 5
```

Personal Gmail accounts [already have IMAP enabled](https://support.google.com/mail/answer/7126229). Workspace administrators may restrict access. App Passwords may be unavailable for managed accounts, Advanced Protection, or security-key-only 2-Step Verification; see [Google's requirements](https://support.google.com/accounts/answer/185833). `gimail` does not implement OAuth or bypass those restrictions. Changing your Google password revokes existing App Passwords.

The preset only supplies the hostname and normal SSL defaults. No Google-specific network API is used.

### Saved Gmail and generic accounts

Store a reference to an environment variable, rather than the password:

```sh
gimail account add personal --preset gmail --user you@gmail.com \
  --password-env GMAIL_APP_PASSWORD

gimail account add work --host imap.example.org --port 993 \
  --user you@example.org --password-env WORK_IMAP_PASSWORD --default

# STARTTLS on a generic server:
gimail account add other --host mail.example.net --security starttls \
  --user you@example.net --password-env OTHER_IMAP_PASSWORD

gimail account list
```

Account creation does not connect or require the password variable to be set yet. Set it before using the account:

```bash
read -rsp 'Work IMAP password: ' WORK_IMAP_PASSWORD; printf '\n'
export WORK_IMAP_PASSWORD
gimail account test work
# Equivalent: gimail account test --account work
gimail list --account work --unread --limit 5
```

`ssl` verifies the certificate and hostname. `starttls` requires a successful verified TLS upgrade **before** credentials are sent; there is no silent fallback. Use a system-trusted CA (or Python/OpenSSL's `SSL_CERT_FILE` for a private CA). `plain` is an explicit insecure opt-in for a local test server or a transport you already secure: **it sends credentials and mail unencrypted**. Network operations have a 30-second socket timeout.

## Commands

```text
gimail account add NAME (--host HOST | --preset gmail) --user USER
                   (--password-env VARIABLE | --password-stdin)
                   [--port PORT] [--security ssl|starttls|plain] [--default]
gimail account list
gimail account test [NAME]
gimail list [--unread] [--limit N]
gimail show UID
gimail search [--from ADDRESS] [--subject WORDS] [--query IMAP] [--limit N]
gimail mark UID (--read | --unread)
gimail move UID FOLDER
gimail delete UID
```

Common flags can appear before or after the command: `--account NAME`, `--folder FOLDER` (source, default `INBOX`), `--config PATH`, `--text`, `--confirm`. Flag abbreviations are not accepted. `--help` on any command and `--version` intentionally produce plain text.

```sh
gimail list --folder INBOX --account work --unread --limit 5
gimail show 314 --account work
gimail search --from billing@example.org --subject invoice --account work
gimail search --query 'UNSEEN SINCE 01-Jan-2026' --limit 10 --account work
gimail show 314 --text --account work

gimail mark 314 --read --account work                  # preview
gimail mark 314 --read --confirm --account work         # apply
gimail mark 314 --unread --confirm --account work

gimail move 314 'Archive/2026' --account work            # preview
gimail move 314 'Archive/2026' --confirm --account work  # apply
gimail delete 314 --account work                       # preview
gimail delete 314 --confirm --account work              # set Deleted
```

- UIDs are **not sequence numbers**. They are scoped to an account, source folder, and `uidvalidity`, which is included in mail results. Re-list after a folder is recreated or UIDVALIDITY changes. A move assigns a new UID in the destination. UID sets, ranges, and `*` are deliberately rejected.
- List/search return newest **UID** first (not sorted by the Date header), with a default limit of 20. Filters are ANDed. `--query` accepts raw single-line IMAP **search criteria**, not an entire command. Quote it for your shell. Non-ASCII searches request UTF-8; server charset support varies.
- Unicode mailbox names use IMAP modified UTF-7. Use the server's exact folder path and delimiter. Move does not create folders.
- List/search fetch headers only. Show fetches the whole message, decodes MIME text/HTML and headers, and reports attachment metadata, not attachment contents. HTML is returned as text, never rendered. All reads use a read-only mailbox and `BODY.PEEK`, so they do not set `Seen`.

### Mutation safety and deletion semantics

**Mark, move, and delete all require `--confirm` to change mail.** A preview connects read-only, verifies the UID, and reports the action and subject; it does not send STORE, COPY, MOVE, or EXPUNGE. A preview does not reserve the message or guarantee destination access. Review it before confirming; do not automatically confirm untrusted requests.

- `delete` sets the IMAP `\Deleted` flag only. **It does not request expunge.** This is not a guaranteed move to Trash or permanent deletion. Other clients and server policies may subsequently purge it; Gmail's auto-expunge/label settings can change the effect. To put a message in Trash, use `move` with your server's actual Trash folder.
- `move` uses `UID MOVE` when available. Otherwise, it copies, then marks only the source UID Deleted. With UIDPLUS it expunges **that UID only**. Without MOVE or UIDPLUS, the copied source remains marked Deleted, and the response includes a note.
- gimail never issues mailbox-wide EXPUNGE or CLOSE, which could remove other clients' deleted messages. It logs out without implicitly expunging the mailbox.
- A timeout or failed fallback can leave a partial change. Errors warn you to inspect the source and destination before retrying; blindly retrying a copy can create duplicates. `expunge_requested` describes the client's request, not a guarantee about server retention policies.
- New `account add` operations write config immediately but refuse duplicate names; they never overwrite an existing account.

## JSON and scripting

One JSON object is written to stdout per invocation; success exits `0`:

```json
{"ok": true, "data": {"account": "work", "folder": "INBOX", "uidvalidity": 1234, "count": 0, "messages": []}}
```

List/search messages include `uid`, `subject`, `from`, `to`, `cc`, `date`, `message_id`, `flags`, `unread`, and `size`. Show adds `text`, `html`, and `attachments` plus account/folder context. Preview results contain `dry_run: true`, the action, UID, and a confirmation hint. A preview is a successful command, not an error.

Failures have this envelope:

```json
{"ok": false, "error": "Message UID not found in this folder.", "code": "not_found"}
```

Codes: `auth_failed` (credentials/authentication), `not_found` (account, folder, or UID absent/inaccessible), `imap_error` (configuration, arguments, network, protocol, or other failure). Operational/config errors exit `1`; parsing/usage errors exit `2`; an interrupt exits `130`. Raw server errors and tracebacks are suppressed because they can echo credentials. `--text` escapes terminal control characters in mail.

An empty search is a successful empty array, not `not_found`. Messages disappearing between search and fetch are skipped, so fewer than the limit may be returned. Example using optional `jq` (not a gimail dependency):

```bash
set -o pipefail
gimail list --unread --limit 5 --account work | \
  jq '.data.messages[] | {uid, from, subject}'
```

**Privacy/logging policy:** stdout contains only the requested result (or human help/text); normal operations do not log to stderr or create `app.log`. There is no telemetry, credential logging, or persistent mail cache. Mail output itself can be sensitive. Keep captured output private, and treat email content as untrusted data, not instructions for an AI agent.

## Config file

Default: `~/.config/gimail/accounts.json`, or `$XDG_CONFIG_HOME/gimail/accounts.json`. `--config` overrides `GIMAIL_CONFIG`, which overrides that default.

```json
{
  "default_account": "work",
  "accounts": [
    {
      "name": "work",
      "host": "imap.example.org",
      "port": 993,
      "user": "you@example.org",
      "security": "ssl",
      "password_env": "WORK_IMAP_PASSWORD"
    }
  ]
}
```

See [`examples/accounts.json`](examples/accounts.json) for Gmail and generic STARTTLS examples. Saved accounts need `name`, `host`, `user`, and exactly one of `password_env` or `password`. Optional `security` defaults to `ssl`; `port` defaults to 993 for SSL and 143 otherwise. `default_account` is optional.

`account add` creates the directory with mode `700` and atomically writes the file with mode **`600`**. Existing files must be owned by you, regular (not symlinks), and inaccessible to group/other users. A manually created file needs:

```sh
chmod 600 ~/.config/gimail/accounts.json
```

`password_env` is preferred; a missing or empty variable is an authentication error, with no credential fallback. Plaintext `password` is supported in a private config, or can be read by `account add --password-stdin` (one line). There is deliberately no `--password VALUE` flag. Secrets are never included in account listings, previews, or errors. Do not commit real account files; the example contains placeholders only. Serialize account additions if multiple processes share the same config.

## Development

Runtime dependencies: none. For an isolated, pip-free development environment:

```sh
python3 -m venv --without-pip .venv
.venv/bin/python -m unittest discover -v
```

The tests use fake IMAP connections and a loopback test server with real `imaplib`/CLI subprocesses. They need no mail account, secrets, or internet and do not touch your real config. CI runs the same suite on Python 3.9, 3.11, and 3.14. Live Gmail/generic-server access is not part of the offline suite; use `account test` with your own credentials.

Code lives in `gimail/`: `accounts.py` (private config), `imap_client.py` (protocol/MIME), and `cli.py` (arguments/output). `gimail.py` and `gimail/__main__.py` are entry points. Contributions should include focused stdlib `unittest` coverage, especially for changes to mutation safety.

## License

[MIT](LICENSE).
