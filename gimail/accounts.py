"""Account configuration. Never include credentials in diagnostic messages."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .errors import GimailError


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECURITIES = ("ssl", "starttls", "plain")


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
        if (password_env is None) == (password is None):
            raise GimailError("Set exactly one of password_env or password for each account.")
        return cls(record["name"], record["host"], port, record["user"], security, password_env, password)

    def public(self):
        result = {
            "name": self.name, "host": self.host, "port": self.port,
            "user": self.user, "security": self.security,
            "credential_source": "environment" if self.password_env else "stored_password",
        }
        if self.password_env:
            result["password_env"] = self.password_env
        return result

    def record(self):
        result = {key: getattr(self, key) for key in ("name", "host", "port", "user", "security")}
        if self.password_env:
            result["password_env"] = self.password_env
        else:
            result["password"] = self.password
        return result

    def secret(self):
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


def add_account(path, account, make_default=False):
    accounts, default = load_config(path)
    if any(existing.name == account.name for existing in accounts):
        raise GimailError("Account already exists; refusing to overwrite it.")
    accounts.append(account)
    if make_default or default is None:
        default = account.name
    save_config(path, accounts, default)
    return account.public()


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
