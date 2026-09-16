"""Account configuration. Never include credentials in diagnostic messages."""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import GimailError
from .keyring import lookup_password


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECURITIES = ("ssl", "starttls", "plain")
CREDENTIAL_FIELDS = frozenset(("password_env", "password", "password_keyring"))


def clean_string(value):
    return isinstance(value, str) and bool(value) and not any(
        ord(char) < 32 or ord(char) == 127 for char in value
    )


@dataclass
class Account:
    name: str
    host: str
    port: int
    user: str
    security: str = "ssl"
    password_env: Optional[str] = None
    password: Optional[str] = field(default=None, repr=False)
    password_keyring: bool = False

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict):
            raise GimailError("Each account must be a JSON object.")
        for key in ("name", "host", "user"):
            if not clean_string(record.get(key)):
                raise GimailError("Each account needs nonempty name, host, and user fields without control characters.")
        security = record.get("security", "ssl")
        if security not in SECURITIES:
            raise GimailError("Account security must be ssl, starttls, or plain.")
        port = record.get("port", 993 if security == "ssl" else 143)
        if type(port) is not int or not 1 <= port <= 65535:
            raise GimailError("Account port must be an integer between 1 and 65535.")
        password_env, password = record.get("password_env"), record.get("password")
        if password_env is not None and (
            not isinstance(password_env, str) or not ENV_NAME.fullmatch(password_env)
        ):
            raise GimailError("password_env must be a valid environment variable name.")
        if password is not None and not clean_string(password):
            raise GimailError("Stored passwords must be nonempty strings without control characters.")
        password_keyring = record.get("password_keyring", False)
        if type(password_keyring) is not bool:
            raise GimailError("password_keyring must be a boolean.")
        if sum((password_env is not None, password is not None, password_keyring)) != 1:
            raise GimailError("Set exactly one credential source: password_env, password, or password_keyring=true.")
        return cls(record["name"], record["host"], port, record["user"], security, password_env, password, password_keyring)

    def public(self):
        result = {
            "name": self.name, "host": self.host, "port": self.port,
            "user": self.user, "security": self.security,
            "credential_source": ("keyring" if self.password_keyring else
                                  "environment" if self.password_env else "stored_password"),
        }
        if self.password_env:
            result["password_env"] = self.password_env
        return result

    def record(self):
        result = {key: getattr(self, key) for key in ("name", "host", "port", "user", "security")}
        if self.password_keyring:
            result["password_keyring"] = True
        elif self.password_env:
            result["password_env"] = self.password_env
        else:
            result["password"] = self.password
        return result

    def secret(self):
        if self.password_keyring:
            password = lookup_password(self.name)
        else:
            password = os.environ.get(self.password_env) if self.password_env else self.password
        if not clean_string(password):
            raise GimailError("Password is missing, empty, or contains unsupported control characters. Check the account's credential source.", "auth_failed")
        return password


def config_path(value=None):
    if value is not None:
        return Path(value).expanduser()
    if os.environ.get("GIMAIL_CONFIG"):
        return Path(os.environ["GIMAIL_CONFIG"]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "gimail" / "accounts.json"


def load_config(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    except FileNotFoundError:
        return [], None
    except OSError:
        raise GimailError("Cannot open accounts config; use a regular file owned by you with mode 600.") from None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise GimailError("Accounts config must be a regular private file. Run chmod 600 on it.")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise GimailError("Accounts config must be owned by the current user.")
            raw = json.load(stream)
    except (OSError, ValueError):
        raise GimailError("Cannot read accounts config: expected valid UTF-8 JSON.") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
        raise GimailError('Accounts config must contain an "accounts" array.')
    accounts = [Account.from_record(record) for record in raw["accounts"]]
    names = [account.name for account in accounts]
    if len(names) != len(set(names)):
        raise GimailError("Account names must be unique.")
    default = raw.get("default_account")
    if default is not None and (not isinstance(default, str) or default not in names):
        raise GimailError("default_account must name an account in the config.")
    return accounts, default


def save_config(path, accounts, default):
    payload = {"accounts": [account.record() for account in accounts]}
    if default is not None:
        payload["default_account"] = default
    temporary = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".accounts-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise GimailError("Could not securely save accounts config.") from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def config_lock(path):
    """Protect read-modify-write operations; never hold this lock while prompting."""
    fd = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_uid != os.getuid()):
            raise GimailError("Accounts lock must be a regular private file owned by you, with mode 600.")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GimailError("Accounts config is busy. Retry after the other writer finishes.") from None
        yield
    except OSError:
        raise GimailError("Could not securely lock accounts config.") from None
    finally:
        if fd is not None:
            os.close(fd)


def add_account(path, account, make_default=False):
    with config_lock(path):
        accounts, default = load_config(path)
        if any(existing.name == account.name for existing in accounts):
            raise GimailError("Account already exists; refusing to overwrite it.")
        accounts.append(account)
        if make_default or default is None:
            default = account.name
        save_config(path, accounts, default)
    return account.public()


def saved_account_index(accounts, name):
    for index, account in enumerate(accounts):
        if account.name == name:
            return index
    raise GimailError("Named account not found in config.", "not_found")


def update_account(path, name, changes, make_default=False, confirm=False):
    allowed = {"host", "port", "user", "security"} | CREDENTIAL_FIELDS
    if not changes and not make_default:
        raise GimailError("No updates supplied. Provide an account field or --default.", exit_status=2)
    changed_sources = set(changes) & CREDENTIAL_FIELDS
    if set(changes) - allowed or len(changed_sources) > 1:
        raise GimailError("Unsupported account fields or conflicting credential sources.", exit_status=2)
    with (config_lock(path) if confirm else nullcontext()):
        accounts, default = load_config(path)
        index = saved_account_index(accounts, name)
        before = accounts[index].record()
        record = {**before, **changes}
        if changed_sources:
            for key in CREDENTIAL_FIELDS - changed_sources:
                record.pop(key, None)
        updated = Account.from_record(record)
        after = updated.record()
        changed_fields = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        next_default = name if make_default else default
        if next_default != default:
            changed_fields.append("default_account")
        result = {
            "action": "account_update", "account": name, "dry_run": not confirm,
            "changed_fields": changed_fields,
            "default_account": next_default or accounts[0].name,
        }
        if confirm and changed_fields:
            accounts[index] = updated
            save_config(path, accounts, next_default)
        elif not confirm:
            result["hint"] = "Re-run account update with the same fields and --confirm to apply."
        return result


def remove_account(path, name, confirm=False, expected_account=None):
    with (config_lock(path) if confirm else nullcontext()):
        accounts, default = load_config(path)
        index = saved_account_index(accounts, name)
        if expected_account is not None and accounts[index] != expected_account:
            raise GimailError("Selected account changed while choosing. Nothing was removed; run account remove again.")
        remaining = accounts[:index] + accounts[index + 1:]
        next_default = (remaining[0].name if remaining else None) if default == name else default
        result = {
            "action": "account_remove", "account": name, "dry_run": not confirm,
            "default_account": next_default or (remaining[0].name if remaining else None),
            "remaining_accounts": len(remaining),
            "note": "Removes only the saved profile; mail, environment variables, and keyring entries are unchanged.",
        }
        if confirm:
            save_config(path, remaining, next_default)
        else:
            result["hint"] = "Re-run account remove with this account name and --confirm to apply."
        return result


def environment_account():
    preset = os.environ.get("GIMAIL_PRESET")
    host = os.environ.get("GIMAIL_HOST")
    if host is None and preset is None:
        return None
    if preset is not None and preset != "gmail":
        raise GimailError("GIMAIL_PRESET must be gmail, or unset for generic IMAP.")
    security = os.environ.get("GIMAIL_SECURITY", "ssl")
    try:
        port = int(os.environ.get("GIMAIL_PORT", "993" if security == "ssl" else "143"))
    except ValueError:
        raise GimailError("GIMAIL_PORT must be an integer between 1 and 65535.") from None
    return Account.from_record({
        "name": "env", "host": host or ("imap.gmail.com" if preset == "gmail" else None),
        "port": port, "user": os.environ.get("GIMAIL_USER"), "security": security,
        "password_env": os.environ.get("GIMAIL_PASSWORD_ENV", "GIMAIL_PASSWORD"),
    })


def select_account(path, name=None):
    name = name if name is not None else os.environ.get("GIMAIL_ACCOUNT")
    if name is None:
        account = environment_account()
        if account is not None:
            return account
    accounts, default = load_config(path)
    if name is not None:
        for account in accounts:
            if account.name == name:
                return account
        raise GimailError("Named account not found in config.", "not_found")
    if not accounts:
        raise GimailError("No account configured. Use account add, or set GIMAIL_HOST (or GIMAIL_PRESET), GIMAIL_USER, and GIMAIL_PASSWORD.", "not_found")
    return next((account for account in accounts if account.name == default), accounts[0])
