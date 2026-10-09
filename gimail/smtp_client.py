"""Plain-text SMTP submission; previews never connect or resolve credentials."""
from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid

from .accounts import clean_string
from .errors import GimailError


# Deliberately accept only bare ASCII dot-atom mailboxes and DNS-style domains.
# Display names, quoted local parts, address lists and SMTPUTF8 are out of scope.
ADDRESS = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)


def address(value):
    if not isinstance(value, str) or len(value) > 254 or not ADDRESS.fullmatch(value):
        raise GimailError("Use a bare ASCII email address, without display names or address lists.", "smtp_error", 2)
    if len(value.split("@", 1)[0]) > 64:
        raise GimailError("Email local part is too long.", "smtp_error", 2)
    return value


def compose(sender, recipients, subject, body):
    sender = address(sender)
    recipients = list(dict.fromkeys(address(value) for value in recipients))
    if not recipients:
        raise GimailError("At least one recipient is required.", "smtp_error", 2)
    if not clean_string(subject):
        raise GimailError("Subject must be nonempty without control characters.", "smtp_error", 2)
    if not isinstance(body, str) or "\x00" in body:
        raise GimailError("Body must be UTF-8 text without NUL characters.", "smtp_error", 2)
    message = EmailMessage(policy=SMTP)
    try:
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
        message.set_content(body, charset="utf-8", cte="quoted-printable")
        message.as_bytes()  # Validate encoding before any connection or keyring lookup.
    except (ValueError, UnicodeError):
        raise GimailError("Message cannot be encoded as UTF-8 email.", "smtp_error", 2) from None
    return message, sender, recipients


def send(account, sender, recipients, subject, body, confirm=False):
    if account.smtp_host is None:
        raise GimailError("SMTP is not configured. Set --smtp-host with account add/update, or GIMAIL_SMTP_HOST.", "smtp_error")
    message, sender, recipients = compose(account.user if sender is None else sender, recipients, subject, body)
    result = {
        "action": "send", "account": account.name, "dry_run": not confirm,
        "smtp_host": account.smtp_host, "smtp_port": account.smtp_port,
        "smtp_security": account.smtp_security,
        "from": sender, "to": recipients, "subject": subject,
        "message_id": str(message["Message-ID"]),
    }
    if not confirm:
        return {**result, "body": body, "delivery": "not_sent",
                "hint": "Review recipients and body, then re-run with the same input and --confirm. Preview does not test SMTP access."}

    client = None
    submitting = False
    try:
        password = account.secret()
        context = ssl.create_default_context()
        if account.smtp_security == "ssl":
            client = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=30)
            client.ehlo()
            client.starttls(context=context)  # No credential transmission or fallback before verified TLS.
            client.ehlo()
        client.login(account.user, password)
        submitting = True
        refused = client.send_message(message, from_addr=sender, to_addrs=recipients)
        # Never expose server diagnostics: they can echo credentials or mail content.
        accepted = [value for value in recipients if value not in refused]
        rejected = [value for value in recipients if value in refused]
        result.update(delivery="partial" if rejected else "accepted", accepted=accepted, refused=rejected)
        result["note"] = "Accepted by the SMTP server, not proof of inbox delivery. No Sent-folder copy is saved by gimail."
        if rejected:
            raise GimailError("Some recipients were refused; others were accepted. Do not resend to accepted recipients.",
                              "partial_delivery", data=result)
        return result
    except smtplib.SMTPAuthenticationError:
        raise GimailError("SMTP authentication failed. Check the account's credentials.", "auth_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise GimailError("SMTP server refused the submission; no recipients were accepted for this message.",
                          "smtp_error", data={**result, "delivery": "not_sent"}) from None
    except (OSError, smtplib.SMTPException, UnicodeError):
        if submitting:
            raise GimailError("SMTP submission failed with unknown delivery status. Inspect server state before retrying; resending can duplicate mail.",
                              "delivery_unknown", data={**result, "delivery": "unknown"}) from None
        raise GimailError("SMTP connection or TLS/authentication setup failed; message was not submitted.", "smtp_error") from None
    finally:
        if client is not None:
            # A failed QUIT/close after DATA acknowledgement must not turn success into a retryable error.
            try:
                client.close()
            except Exception:
                pass
