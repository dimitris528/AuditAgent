"""
Outbound mail for the password-reset flow.

There is no mail transport configured in this deployment, and this module is
deliberately honest about that rather than pretending otherwise. Without SMTP
credentials `send_password_reset` LOGS the reset link at WARN and returns
False — a visible stub. The alternative, swallowing the send and returning
True, would leave the whole flow looking healthy while every user waited
forever for an email that was never going to arrive.

Wiring real delivery
--------------------
Set the SMTP_* variables and it sends for real; nothing else changes. The
provider is deliberately not baked in — SMTP is what every transactional
provider (SES, Postmark, Resend, Mailgun, plain Gmail) exposes, so this works
with all of them and needs no SDK.

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_STARTTLS (default on)

RESET_TOKEN_IN_RESPONSE
-----------------------
A LOCAL DEVELOPMENT switch that returns the reset token in the API response so
the flow can be exercised end to end without a mailbox. It refuses to turn on
when a production database is configured: returning a reset token to an
unauthenticated caller is account takeover as a feature, and this is exactly
the flag someone leaves on by accident.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage

from config import APP_BASE_URL

SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "587").strip() or 587)
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_FROM = (os.getenv("SMTP_FROM") or SMTP_USER or "no-reply@localhost").strip()
SMTP_STARTTLS = (os.getenv("SMTP_STARTTLS", "1") or "1").strip() != "0"

_EXPOSE_REQUESTED = (os.getenv("RESET_TOKEN_IN_RESPONSE", "0") or "0").strip() == "1"


def _production_database():
    """True when a real (non-SQLite) database is configured.

    Used only to refuse the token-exposure switch. Imported lazily inside the
    function so this module stays importable with no database at all.
    """
    try:
        from server import database

        return database.is_configured() and database.is_postgres()
    except Exception:      # pragma: no cover - defensive
        return True        # fail CLOSED: unknown means "assume production"


#: Whether the API may echo the reset token. See the module docstring.
EXPOSE_RESET_TOKEN = _EXPOSE_REQUESTED and not _production_database()

if _EXPOSE_REQUESTED and not EXPOSE_RESET_TOKEN:
    print("[WARN] RESET_TOKEN_IN_RESPONSE is set but a production database is "
          "configured — refusing to return reset tokens over the API.")


def is_configured():
    """True when a real send is possible."""
    return bool(SMTP_HOST and SMTP_FROM)


def reset_link(token):
    """The URL that goes in the email. Points at the FRONTEND, not the API —
    the user needs the form, not the endpoint."""
    return f"{APP_BASE_URL}/reset-password?token={token}"


_SUBJECT = "Επαναφορά κωδικού — ΛογιστήριοPro"

_BODY = """Λάβαμε αίτημα επαναφοράς του κωδικού σας.

Ανοίξτε τον παρακάτω σύνδεσμο για να ορίσετε νέο κωδικό:

{link}

Ο σύνδεσμος λήγει σε {minutes} λεπτά και μπορεί να χρησιμοποιηθεί μία φορά.

Αν δεν ζητήσατε εσείς την επαναφορά, αγνοήστε αυτό το μήνυμα — ο κωδικός σας
παραμένει αμετάβλητος.
"""


def send_password_reset(to_email, link, minutes=None):
    """Send the reset link. Returns True when it actually went out.

    NEVER raises: a mail failure must not turn into a 500 on the forgot-password
    endpoint, because the endpoint's whole contract is to answer identically
    whatever happens — and a 500 for a real address versus a 200 for an unknown
    one is precisely the oracle that contract exists to deny.
    """
    if minutes is None:
        from server.store import RESET_TOKEN_TTL_MINUTES

        minutes = RESET_TOKEN_TTL_MINUTES
    body = _BODY.format(link=link, minutes=minutes)

    if not is_configured():
        # The visible stub. Logged so a developer (or an operator diagnosing a
        # "no email arrived" report) can complete the flow by hand.
        print(f"[WARN] SMTP is not configured — password reset for {to_email} "
              f"was NOT emailed. Link: {link}")
        return False

    message = EmailMessage()
    message["Subject"] = _SUBJECT
    message["From"] = SMTP_FROM
    message["To"] = to_email
    message.set_content(body)

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10,
                                  context=ssl.create_default_context()) as smtp:
                _login_and_send(smtp, message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
                if SMTP_STARTTLS:
                    smtp.starttls(context=ssl.create_default_context())
                _login_and_send(smtp, message)
        print(f"[INFO] Password reset email sent to {to_email}.")
        return True
    except Exception as exc:
        # Logged in full, never surfaced — see the docstring.
        print(f"[ERROR] Could not send the password reset email: "
              f"{type(exc).__name__}: {exc}")
        return False


def _login_and_send(smtp, message):
    if SMTP_USER:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
    smtp.send_message(message)
