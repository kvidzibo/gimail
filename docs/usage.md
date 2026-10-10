# Usage and configuration

[Quickstart](../README.md) · [Development](development.md)

Installed commands work from any directory. Checkout-relative commands below run from the repository root.

## Install

Python **3.9+** on Debian/Linux or another POSIX system.

### Recommended: uv tool install

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
uv tool install git+https://github.com/kvidzibo/gimail
gimail --help
```

uv creates an isolated environment and installs the `gimail` command. It works from any directory; no checkout or manual symlink is needed. The Git URL explicitly selects this repository rather than a same-named package from a package index.

Keep uv's tool-bin directory on `PATH` (`uv tool dir --bin` shows it; usually `~/.local/bin`). If an old manual `gimail` symlink conflicts, remove only that symlink before installing rather than force-overwriting an unrelated executable.

```sh
uv tool upgrade gimail
uv tool uninstall gimail
```

Uninstalling the tool leaves your account config and keyring entries intact. To pin an installation, append `@<commit-or-tag>` to the Git URL; upgrades respect that reference.

For a one-off run without a persistent tool installation:

```sh
uvx --from git+https://github.com/kvidzibo/gimail gimail --help
```

### Run directly from a clone

uv is optional. The original entry points need no package installation; use a project-local environment:

```sh
git clone https://github.com/kvidzibo/gimail.git
cd gimail
python3 -m venv --without-pip .venv
.venv/bin/python gimail.py --help
.venv/bin/python -m gimail --help
```

The examples below use the installed `gimail` command. From an uninstalled clone, substitute `.venv/bin/python gimail.py`.

## First run: environment variables only

No accounts file is needed. For a generic IMAP server, in **Bash**:

```bash
unset GIMAIL_ACCOUNT GIMAIL_PASSWORD_ENV GIMAIL_PRESET GIMAIL_PORT GIMAIL_SECURITY
unset GIMAIL_SMTP_HOST GIMAIL_SMTP_PORT GIMAIL_SMTP_SECURITY
export GIMAIL_HOST=imap.example.org
export GIMAIL_USER=you@example.org
read -rsp 'IMAP password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
gimail list --unread --limit 5
```

Reading the password this way keeps it out of shell history and command-line arguments. A secret manager can supply the same environment variable. `gimail` does not load `.env` files automatically.

| Variable | Meaning |
| --- | --- |
| `GIMAIL_HOST` | IMAP hostname; activates environment-only account `env` |
| `GIMAIL_PRESET` | `gmail` supplies `imap.gmail.com` and `smtp.gmail.com`; also activates `env` |
| `GIMAIL_USER` | Login username, usually a full email address |
| `GIMAIL_PASSWORD` | Password for the environment-only account |
| `GIMAIL_PASSWORD_ENV` | Optional alternative variable **name** containing that password |
| `GIMAIL_SECURITY` | `ssl` (default), `starttls`, or `plain` |
| `GIMAIL_PORT` | Default `993` for SSL; `143` otherwise |
| `GIMAIL_SMTP_HOST` | Optional SMTP hostname; requires environment account setup above |
| `GIMAIL_SMTP_SECURITY` | `ssl` (default) or `starttls`; no plaintext SMTP |
| `GIMAIL_SMTP_PORT` | Default `465` for SMTP SSL; `587` for STARTTLS |
| `GIMAIL_ACCOUNT` | Select a saved account instead of environment-only setup |
| `GIMAIL_CONFIG` | Override the accounts file path |

`--account NAME` wins over `GIMAIL_ACCOUNT`. Either explicitly selects a **saved** account. Otherwise, an environment-only account wins over the saved default. Without environment setup, `default_account` or the first saved account is used.

### Gmail App Password

1. Turn on Google [2-Step Verification](https://support.google.com/accounts/answer/185839).
2. Create a dedicated password at [Google App Passwords](https://myaccount.google.com/apppasswords). Use that password, **not your normal Google password**. Enter its 16 characters without the display spaces.
3. Use Gmail's preset:

```bash
unset GIMAIL_HOST GIMAIL_PORT GIMAIL_SECURITY GIMAIL_ACCOUNT GIMAIL_PASSWORD_ENV
unset GIMAIL_SMTP_HOST GIMAIL_SMTP_PORT GIMAIL_SMTP_SECURITY
export GIMAIL_PRESET=gmail
export GIMAIL_USER=you@gmail.com
read -rsp 'Gmail App Password: ' GIMAIL_PASSWORD; printf '\n'
export GIMAIL_PASSWORD
gimail account test
gimail list --unread --limit 5
```

Personal Gmail accounts [already have IMAP enabled](https://support.google.com/mail/answer/7126229). Workspace administrators may restrict access. App Passwords may be unavailable for managed accounts, Advanced Protection, or security-key-only 2-Step Verification; see [Google's requirements](https://support.google.com/accounts/answer/185833). `gimail` does not implement OAuth or bypass those restrictions. Changing your Google password revokes existing App Passwords.

The preset supplies IMAP/SMTP hostnames and normal SSL defaults when creating an account or using environment setup. Existing saved profiles are not modified; enable SMTP with `account update` as shown below. No Google-specific network API is used.

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

`ssl` verifies the certificate and hostname. `starttls` requires a successful verified TLS upgrade **before** credentials are sent; there is no silent fallback. Use a system-trusted CA (or Python/OpenSSL's `SSL_CERT_FILE` for a private CA). `plain` is an explicit insecure opt-in for a local test server or a transport you already secure: **it sends credentials and mail unencrypted**. Network operations have a 30-second socket timeout. IMAP authentication uses LOGIN for ASCII credentials, or SASL PLAIN for non-ASCII credentials and servers advertising `LOGINDISABLED`; those cases require server support for `AUTH=PLAIN`.

### GNOME Keyring / Seahorse (optional)

Gimail can retrieve a saved account's password from **GNOME Keyring** itself. **Seahorse** is the GUI for managing/unlocking the keyring; gimail calls libsecret's `secret-tool` internally. No Python packages are needed. Environment/stored-password accounts do not require this helper.

On a Debian desktop, install these system packages if needed:

```sh
sudo apt install gnome-keyring libsecret-tools seahorse
```

**One-time secret setup**, in your own terminal:

```sh
secret-tool store --label='gimail personal' service gimail account personal
```

Enter your IMAP password at the prompt (a Gmail **App Password** for Gmail), never as a command-line argument. This creates or updates the matching entry; it does not change your provider's password. It appears in Seahorse as `gimail personal`. The lookup attributes are **`service=gimail`** and **`account=<saved profile name>`**. The label is just for display: a manually created item with the same label but different attributes will not match. Use the corresponding profile name for other accounts. These keys do not include the config path, host, or login username: profiles with the same name share an entry. Use distinct profile names when credentials differ.

Then opt an **existing profile** into keyring authentication:

```sh
gimail account update personal --keyring          # preview
gimail account update personal --keyring --confirm
```

Or choose keyring when creating a **new profile**:

```sh
gimail account add personal --preset gmail --user you@gmail.com --keyring
```

After setup, **humans and agents use ordinary commands**—no password exports, command substitution, or special agent rules:

```sh
gimail account test personal
gimail --account personal list --unread --limit 5
gimail --account personal show 314
```

- `--keyring` saves only `"password_keyring": true`; it replaces another credential source but does not create, copy, update, or delete a keyring entry. Account add/list/update/remove—including previews—never query the keyring. Switching away or removing a profile leaves its keyring entry alone.
- Gimail looks up the password only when authenticating. The helper's stdout is captured internally and stderr discarded; credentials are never added to command arguments, config, or normal CLI output. Spaces are preserved exactly; invalid UTF-8 and unsupported password control characters are rejected, not trimmed.
- The keyring must be accessible in the process's desktop/user D-Bus session. Unlock it in Seahorse first. A locked keyring may request an interactive unlock; lookup times out after **15 seconds** with `auth_failed`. Missing helper, missing entry, or session/access failure also returns `auth_failed`, with no credential fallback.
- SSH, cron, containers, and agents running as another user do not automatically have access to your desktop keyring. Configure an appropriate session/credential source instead of disabling keyring protection. Keyring storage is not a sandbox against processes running as your user.
- The helper is resolved from `PATH`; use a trusted `secret-tool` installation. Keyring support is optional and requires no changes to generic IMAP authentication or TLS behavior. Use explicit `--account personal` to avoid an old environment-only configuration taking precedence.

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
- Update requires an explicit `NAME`; removal accepts `NAME`, `--account NAME`, or the picker. `GIMAIL_ACCOUNT` and environment-only setup do not implicitly choose a profile to update/remove. If both a name and `--account` are supplied, they must match. An explicitly empty name is an error, never a fallback to another account.
- Update supports `--host`, `--port`, `--user`, `--security`, `--smtp-host`, `--smtp-port`, `--smtp-security`, `--password-env`, `--password-stdin`, `--keyring`, and `--default`. Omitted fields stay unchanged: changing security alone does **not** change the port. No field options means an argument error; reapplying existing values is a successful no-op with `changed_fields: []`.
- `--password-env` takes a **variable name, not the password**. Credential options are mutually exclusive: `--password-env`, `--password-stdin`, or `--keyring` replaces the previous source. `--password-stdin` stores plaintext in the config, not in the keyring. `--password-stdin` consumes one input line even for a preview. Update results list changed field names, never credential values.
- Removing the default selects the first remaining saved account. Removing the last leaves a valid empty `accounts` array. Other profiles are retained, and the removed name can be added again.
- Removal does not delete mail, unset your shell variables, delete keyring entries, or revoke provider passwords. If the selected profile changes while the picker is open, removal stops and asks you to retry; reordering accounts cannot redirect a displayed index to a different profile.

## Sending email

SMTP settings are separate from IMAP. Both use the account's **same login username and credential source**, including keyring; different SMTP credentials are not supported. A newly created Gmail preset account includes SMTP SSL on port 465. For an existing profile:

```sh
# Preview, then save the SMTP endpoint:
gimail account update personal --smtp-host smtp.gmail.com
gimail account update personal --smtp-host smtp.gmail.com --confirm

# Generic provider using STARTTLS:
gimail account update work --smtp-host smtp.example.org \
  --smtp-security starttls --smtp-port 587 --confirm
```

Environment-only generic accounts need `GIMAIL_SMTP_HOST` alongside `GIMAIL_HOST`, `GIMAIL_USER`, and the password source. SMTP defaults to verified SSL/465; choose `GIMAIL_SMTP_SECURITY=starttls` for verified STARTTLS/587. SMTP does not support plaintext or downgrade fallback. ASCII credentials use the server's supported `smtplib` mechanisms; non-ASCII credentials require advertised `AUTH PLAIN` and are encoded as UTF-8 over TLS. Settings do not affect IMAP ports/security. On first SMTP setup the port defaults according to SMTP security; later updates preserve omitted fields, so change the port explicitly when switching security.

```sh
# body.txt must contain plain UTF-8 text:
gimail send --account personal --to recipient@example.org \
  --subject 'Hello' --body-file body.txt --text                # preview
gimail send --account personal --to recipient@example.org \
  --subject 'Hello' --body-file body.txt --confirm              # submit

printf 'Hello from stdin.\n' | gimail send --account work \
  --to one@example.org --to two@example.org --subject 'Hello' --body-stdin
```

- **Preview never connects, sends, or retrieves credentials.** Review its SMTP endpoint, sender, recipients, subject, and body before repeating the same input with `--confirm`. It does not test connectivity, authentication, or provider sender permissions. Changed files/settings between invocations change the message; a preview is not a reservation. Preview output contains the body: keep it private.
- The sender defaults to the account username; use `--from you@example.org` if the login is not an email address or to select a provider-authorized alias. Recipients use repeated `--to`; exact duplicates are removed. Addresses must be bare ASCII dot-atom addresses with DNS-style domains, not display names, quoted local parts, comma-separated lists, or SMTPUTF8 addresses. Unicode subject/body are supported. Subjects must be nonempty and contain no control characters.
- Body input is required: `--body-file PATH` or `--body-stdin` (reads all stdin). No attachments, CC/BCC, HTML, reply/thread support, or custom message headers. `send` does not use an IMAP folder.
- Confirmed success reports `delivery: accepted`, the accepted recipients, and the generated Message-ID; SMTP acceptance **does not prove inbox delivery**. Gimail does not append a Sent-folder copy; providers may save one themselves. No automatic retry is performed.
- Partial acceptance exits `1` with `code: partial_delivery` and `data.accepted`/`data.refused`. Do not resend to accepted recipients. A refused submission reports `delivery: not_sent`. A disconnect/timeout during submission reports `code: delivery_unknown` and `delivery: unknown`; inspect server state before retrying to avoid duplicates. An interrupt exits `130`; if it happens during submission, it also preserves `delivery_unknown` and the Message-ID for investigation. Raw SMTP responses are never printed.

## Saving drafts

`draft` uses IMAP to save a plain-text message for review, editing, and sending in Gmail or another mail client. It **never connects to SMTP or sends mail**, and does not require SMTP configuration. It accepts the same sender, recipients, subject, and body inputs as `send`, with the same validation and limitations (including a required recipient and nonempty subject).

```sh
gimail draft --account personal --to recipient@example.org \
  --subject 'Please review' --body-file body.txt --text          # offline preview
gimail draft --account personal --to recipient@example.org \
  --subject 'Please review' --body-file body.txt --confirm       # save, not send
```

- Preview never connects or retrieves credentials. Its `folder: null` means discovery will happen when saving; it does not verify Drafts access. The preview includes the body; keep it private.
- Confirmation lists mailboxes and finds one selectable special-use `\\Drafts` folder, including localized Gmail mailbox names. If none or multiple are advertised, it stops without saving. Override discovery with `--folder 'exact/mailbox/path'`; the folder must already exist. This flag is the **destination**, not a source mailbox. Gmail's Drafts label must be exposed to IMAP.
- Saving appends a new message with the IMAP `\\Draft` flag, without selecting or expunging a mailbox. Successful output reports `saved: true`, the actual folder, and the generated Message-ID; review the draft in Gmail before sending. No automatic retry, replacement of existing drafts, attachment, CC/BCC, HTML, or reply/thread support.
- Each confirmed invocation creates a **new** draft. A rejected APPEND reports `saved: false`; a disconnect, timeout, or interrupt during APPEND reports `code: draft_unknown`, `saved: null`, and the Message-ID. Inspect the destination before retrying to avoid duplicates; an interrupt exits `130`. Raw server diagnostics are suppressed.

## Commands

```text
gimail account add NAME (--host HOST | --preset gmail) --user USER
                   (--password-env VARIABLE | --password-stdin | --keyring)
                   [--port PORT] [--security ssl|starttls|plain] [--default]
                   [--smtp-host HOST] [--smtp-port PORT] [--smtp-security ssl|starttls]
gimail account update NAME [--host HOST] [--port PORT] [--user USER]
                      [--security ssl|starttls|plain]
                      [--password-env VARIABLE | --password-stdin | --keyring] [--default] [--confirm]
                      [--smtp-host HOST] [--smtp-port PORT] [--smtp-security ssl|starttls]
gimail account remove [NAME] [--confirm]
gimail account list
gimail account test [NAME]
gimail list [--unread] [--limit N]
gimail show UID
gimail search [--from ADDRESS] [--subject WORDS] [--query IMAP] [--limit N]
gimail mark UID (--read | --unread)
gimail move UID FOLDER
gimail delete UID
gimail send --to ADDRESS [--to ADDRESS ...] --subject SUBJECT
            (--body-file PATH | --body-stdin) [--from ADDRESS] [--confirm]
gimail draft --to ADDRESS [--to ADDRESS ...] --subject SUBJECT
             (--body-file PATH | --body-stdin) [--from ADDRESS] [--folder FOLDER] [--confirm]
```

Common flags can appear before or after the command where applicable: `--account NAME`, `--config PATH`, `--text`, `--confirm`. Reading/mutation IMAP commands and `account test` also accept `--folder FOLDER` (source, default `INBOX`); `draft` uses it as the destination (default: discover Drafts). Flag abbreviations are not accepted. `--help` on any command and `--version` intentionally produce plain text.

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

Codes: `auth_failed` (credentials/authentication), `not_found` (account, folder, or UID absent/inaccessible), `imap_error` (configuration, arguments, IMAP, or other failure), `smtp_error` (SMTP/message validation or submission failure), `partial_delivery` (some recipients accepted), `delivery_unknown` (submission status uncertain), `draft_unknown` (draft save status uncertain). SMTP delivery and draft save errors can also include safe `data` context as described above. Operational/config errors exit `1`; parsing/usage errors exit `2`; an interrupt exits `130`. Raw server errors and tracebacks are suppressed because they can echo credentials. `--text` escapes terminal control characters in mail.

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

See [`examples/accounts.json`](../examples/accounts.json) for Gmail and generic STARTTLS examples. Saved accounts need `name`, `host`, `user`, and exactly one credential source: `password_env`, `password`, or `password_keyring: true`. `password_keyring` must be a JSON boolean; `false` does not define a credential source. Optional `security` defaults to `ssl`; `port` defaults to 993 for SSL and 143 otherwise. `default_account` is optional. SMTP is opt-in for existing records: optional `smtp_host` enables it, `smtp_security` defaults to `ssl` (or explicit `starttls`), and `smtp_port` defaults to 465/587 respectively. SMTP port/security require a host; saved records without SMTP remain valid and cannot send until configured.

`account add` creates the directory with mode `700` and atomically writes the file with mode **`600`**. Existing directory permissions are left unchanged; keep that directory private. Existing files must be owned by you, regular (not symlinks), and inaccessible to group/other users. A manually created file needs:

```sh
chmod 600 ~/.config/gimail/accounts.json
```

Prefer `password_env` or `password_keyring: true` over plaintext storage. A missing or empty credential is an authentication error, with no credential fallback. Keyring profiles store only the boolean flag and use the fixed attributes documented above. Plaintext `password` is supported in a private config, or can be read by `account add` / `account update` with `--password-stdin` (one line). There is deliberately no `--password VALUE` flag. Secrets are never included in account listings, previews, or errors. Do not commit real account files; the example contains placeholders only. CLI config writers coordinate through an adjacent `accounts.json.lock` file (mode `600`, no credentials). A concurrent writer fails with a busy error rather than overwriting another change; retry after it finishes. The lock is released on exit, but its empty file stays in place. Previews and picker prompts do not acquire a write lock. Do not delete the lock file or manually edit the config while a writer is running.
