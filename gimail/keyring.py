"""Optional Secret Service credentials via libsecret's secret-tool executable.

Seahorse manages these entries; no Python keyring or D-Bus dependency is needed.
Only lookup is supported. Never forward helper output to diagnostics.
"""
import shutil
import subprocess

from .errors import GimailError


LOOKUP_TIMEOUT = 15


def lookup_password(account_name):
    helper = shutil.which("secret-tool")
    if helper is None:
        raise GimailError("Keyring support needs secret-tool on PATH. Install libsecret-tools, or use another credential source.", "auth_failed")
    try:
        result = subprocess.run(
            [helper, "lookup", "--", "service", "gimail", "account", account_name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=LOOKUP_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        raise GimailError("Keyring lookup timed out. Unlock the keyring in Seahorse and retry from the same desktop session.", "auth_failed") from None
    except OSError:
        raise GimailError("Could not run the keyring helper. Check secret-tool and your desktop session.", "auth_failed") from None
    if result.returncode != 0 or not result.stdout:
        raise GimailError("Could not retrieve the keyring password. Unlock the keyring in Seahorse and check the entry with service=gimail and account matching the saved profile name.", "auth_failed")
    try:
        # With piped stdout secret-tool adds no newline. Preserve the secret exactly;
        # Account.secret validates controls rather than silently changing passwords.
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise GimailError("Keyring password must be valid UTF-8 text.", "auth_failed") from None
