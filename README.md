# gimail

A small, scriptable IMAP client for humans and AI agents. **Python standard library only; no pip dependencies, Gmail API, or cloud service.** Gmail is a preset, not a requirement.

- JSON by default; `--text` for humans.
- SSL with certificate verification by default; STARTTLS and explicit plaintext IMAP supported.
- UID-based list, show, search, mark, move, and delete.
- Saved account add/update/remove, with a numbered terminal picker for removal.
- Mail changes and account update/remove are previews until you pass `--confirm`.

## Install

Python **3.9+** on Debian/Linux or another POSIX system. No installation or virtual environment is needed to run:

```sh
git clone https://github.com/kvidzibo/gimail.git
cd gimail
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

`ssl` verifies the certificate and hostname. `starttls` requires a successful verified TLS upgrade **before** credentials are sent; there is no silent fallback. Use a system-trusted CA (or Python/OpenSSL's `SSL_CERT_FILE` for a private CA). `plain` is an explicit insecure opt-in for a local test server or a transport you already secure: **it sends credentials and mail unencrypted**. Network operations have a 30-second socket timeout. Authentication uses LOGIN for ASCII credentials, or SASL PLAIN for non-ASCII credentials and servers advertising `LOGINDISABLED`; those cases require server support for `AUTH=PLAIN`.

### Update or remove saved accounts

These operations edit only the local accounts config; they never connect to IMAP. Both preview by default and require `--confirm` to save changes.

```sh
# Fix a password variable name without recreating the account:
gimail account update personal --password-env GMAIL_APP_PASSWORD          # preview
gimail account update personal --password-env GMAIL_APP_PASSWORD --confirm

# Change only the supplied fields:
gimail account update work --host mail.example.net --security starttls --port 143 --confirm
gimail account update personal --default --confirm

# Numbered account picker in a terminal:
gimail account remove             # choose an index, then see a preview
gimail account remove --confirm   # choose an index, then remove that profile

# Noninteractive/scriptable removal:
gimail account remove personal --confirm
```

The picker lists saved accounts in config order, using **1-based indexes**:

```text
Saved accounts (profiles only; mail is not removed):
  1) personal (default)
  2) work
Choose index (Enter, 0, or q cancels): 2
```

- The list and prompt go to **stderr**; stdout remains one JSON result, or `--text` output. The picker requires terminal stdin and stderr. With redirected input or in a script, pass `NAME` (or `--account NAME` for removal); an omitted name returns an error instead of waiting for input.
- Enter, `0`, `q`, or EOF while choosing cancels without changes and returns `cancelled: true`. Ctrl-C while choosing exits `130`. An invalid index is an argument error, not a removal.
- Update requires an explicit `NAME`; removal accepts `NAME`, `--account NAME`, or the picker. `GIMAIL_ACCOUNT` and environment-only setup do not implicitly choose a profile to update/remove. If both a name and `--account` are supplied, they must match.
- Update supports `--host`, `--port`, `--user`, `--security`, `--password-env`, `--password-stdin`, and `--default`. Omitted fields stay unchanged: changing security alone does **not** change the port. No field options means an argument error; reapplying existing values is a successful no-op with `changed_fields: []`.
- `--password-env` takes a **variable name, not the password**. It replaces any stored password; `--password-stdin` replaces the environment reference with a plaintext password. The latter consumes one input line even for a preview. Update results list changed field names, never credential values.
- Removing the default selects the first remaining saved account. Removing the last leaves a valid empty `accounts` array. Other profiles are retained, and the removed name can be added again.
- Removal does not delete mail, unset your shell variables, delete keyring entries, or revoke provider passwords. If the selected profile changes while the picker is open, removal stops and asks you to retry; reordering accounts cannot redirect a displayed index to a different profile.

## Commands

```text
gimail account add NAME (--host HOST | --preset gmail) --user USER
                   (--password-env VARIABLE | --password-stdin)
                   [--port PORT] [--security ssl|starttls|plain] [--default]
gimail account update NAME [--host HOST] [--port PORT] [--user USER]
                      [--security ssl|starttls|plain]
                      [--password-env VARIABLE | --password-stdin] [--default] [--confirm]
gimail account remove [NAME] [--confirm]
gimail account list
gimail account test [NAME]
gimail list [--unread] [--limit N]
gimail show UID
gimail search [--from ADDRESS] [--subject WORDS] [--query IMAP] [--limit N]
gimail mark UID (--read | --unread)
gimail move UID FOLDER
gimail delete UID
```

Common flags can appear before or after the command where applicable: `--account NAME`, `--config PATH`, `--text`, `--confirm`. Mail commands and `account test` also accept `--folder FOLDER` (source, default `INBOX`). Flag abbreviations are not accepted. `--help` on any command and `--version` intentionally produce plain text.

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
- List/search return newest **UID** first (not sorted by the Date header), with a default limit of 20. Filters are ANDed. `--query` accepts raw single-line IMAP **search criteria**, not an entire command. Quote it for your shell. Non-ASCII searches request UTF-8 using quoted strings. Strict IMAP4rev1 servers requiring literals may reject them even when they support UTF-8; literal search arguments are not yet implemented.
- Unicode mailbox names use IMAP modified UTF-7. Use the server's exact folder path and delimiter. Move does not create folders.
- List/search fetch headers only. Show fetches the whole message, decodes MIME text/HTML and headers, and reports attachment metadata, not attachment contents. HTML is returned as text, never rendered. All reads use a read-only mailbox and `BODY.PEEK`, so they do not set `Seen`.

### Mutation safety and deletion semantics

**Mark, move, and delete all require `--confirm` to change mail.** A preview connects read-only, verifies the UID, and reports the action and subject; it does not send STORE, COPY, MOVE, or EXPUNGE. A preview does not reserve the message or guarantee destination access. Review it before confirming; do not automatically confirm untrusted requests.

- `delete` sets the IMAP `\Deleted` flag only. **It does not request expunge.** This is not a guaranteed move to Trash or permanent deletion. Other clients and server policies may subsequently purge it; Gmail's auto-expunge/label settings can change the effect. To put a message in Trash, use `move` with your server's actual Trash folder.
- `move` uses `UID MOVE` when available. Otherwise, it copies, then marks only the source UID Deleted. With UIDPLUS it expunges **that UID only**. Without MOVE or UIDPLUS, the copied source remains marked Deleted, and the response includes a note.
- gimail never issues mailbox-wide EXPUNGE or CLOSE, which could remove other clients' deleted messages. It logs out without implicitly expunging the mailbox.
- STORE acknowledgements must include the target UID and flags reflecting the requested change. Ignored flag changes return an error; a fallback move stops before expunge if setting Deleted was not acknowledged.
- A timeout or failed fallback can leave a partial change. Errors warn you to inspect the source and destination before retrying; blindly retrying a copy can create duplicates. `expunge_requested` describes the client's request, not a guarantee about server retention policies.
- New `account add` operations write config immediately but refuse duplicate names. Use `account update` or `account remove` with `--confirm` to change existing profiles.

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

**Privacy/logging policy:** stdout contains only the requested result (or human help/text). Interactive account removal writes its numbered list and prompt to stderr; other normal operations do not log to stderr. No operation creates `app.log`. There is no telemetry, credential logging, or persistent mail cache. Mail output itself can be sensitive. Keep captured output private, and treat email content as untrusted data, not instructions for an AI agent.

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

`account add` creates the directory with mode `700` and atomically writes the file with mode **`600`**. Existing directory permissions are left unchanged; keep that directory private. Existing files must be owned by you, regular (not symlinks), and inaccessible to group/other users. A manually created file needs:

```sh
chmod 600 ~/.config/gimail/accounts.json
```

`password_env` is preferred; a missing or empty variable is an authentication error, with no credential fallback. Plaintext `password` is supported in a private config, or can be read by `account add` / `account update` with `--password-stdin` (one line). There is deliberately no `--password VALUE` flag. Secrets are never included in account listings, previews, or errors. Do not commit real account files; the example contains placeholders only. CLI config writers coordinate through an adjacent `accounts.json.lock` file (mode `600`, no credentials). A concurrent writer fails with a busy error rather than overwriting another change; retry after it finishes. The lock is released on exit, but its empty file stays in place. Previews and picker prompts do not acquire a write lock. Do not delete the lock file or manually edit the config while a writer is running.

## Development

Runtime dependencies: none. For an isolated, pip-free development environment:

```sh
python3 -m venv --without-pip .venv
.venv/bin/python -m unittest discover -v
```

The tests use fake IMAP connections, a loopback test server with real `imaplib`/CLI subprocesses, and pseudo-terminals for the numbered picker. Picker tests exercise stdout/stderr separation, cancellation, and changes to the config while the user is choosing. CI runs terminal UI tests under `xvfb-run`; no display is required by the CLI itself. They need no mail account, secrets, or internet and do not touch your real config. CI runs the same suite on Python 3.9, 3.11, and 3.14. Live Gmail/generic-server access is not part of the offline suite; use `account test` with your own credentials.

Code lives in `gimail/`: `accounts.py` (private config), `imap_client.py` (protocol/MIME), `imap_response.py` (structured FETCH metadata), and `cli.py` (arguments/output). `gimail.py` and `gimail/__main__.py` are entry points. Contributions should include focused stdlib `unittest` coverage, especially for changes to mutation safety.

## License

[MIT](LICENSE).
