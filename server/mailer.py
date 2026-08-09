"""
Outbound mail for the password-reset flow.

Without SMTP credentials `send_password_reset` LOGS the reset link at WARN and
returns False — a visible stub. The alternative, swallowing the send and
returning True, would leave the whole flow looking healthy while every user
waited forever for an email that was never going to arrive.

Wiring real delivery
--------------------
Set the SMTP_* variables and it sends for real; nothing else changes. The
provider is deliberately not baked in — SMTP is what every transactional
provider (SES, Postmark, Resend, Mailgun, plain Gmail) exposes, so this works
with all of them and needs no SDK.

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_STARTTLS (default on),
    SMTP_TIMEOUT (default 15), SMTP_DEBUG (default off — see below)

Why this module talks so much
-----------------------------
A send that fails here fails INVISIBLY by design: the endpoint answers the
same 200 whatever happens, because a 500 for a real address next to a 200 for
an unknown one is an account-enumeration oracle. That contract is right, and
it means the log is the only place a failure can ever show up. So every stage
announces itself — configuration, connect, STARTTLS, login, send — and a
failure reports the server's own words: the SMTP status code and response
line, which for a hosted provider IS the provider's API answer.

Every line goes out with flush=True. Under gunicorn/uvicorn stdout is a pipe
rather than a terminal, so Python block-buffers it; an unflushed print can sit
in a 4 KB buffer for minutes or vanish entirely when a worker is recycled,
which reads exactly like code that never ran.

SMTP_DEBUG=1 additionally dumps the whole SMTP conversation. Off by default,
and not only for noise: smtplib prints the AUTH line, and that line contains
your base64-encoded credentials. Turn it on to diagnose, then turn it off and
rotate anything the log saw.

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
import socket
import ssl
import sys
import traceback
from email.message import EmailMessage

from config import APP_BASE_URL

SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "587").strip() or 587)
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_FROM = (os.getenv("SMTP_FROM") or SMTP_USER or "no-reply@localhost").strip()
SMTP_STARTTLS = (os.getenv("SMTP_STARTTLS", "1") or "1").strip() != "0"
SMTP_TIMEOUT = int((os.getenv("SMTP_TIMEOUT") or "15").strip() or 15)

#: 0 off, 1 the conversation, 2 the conversation with timestamps. See the
#: module docstring: level 1 already prints your credentials.
SMTP_DEBUG = int((os.getenv("SMTP_DEBUG") or "0").strip() or 0)

_EXPOSE_REQUESTED = (os.getenv("RESET_TOKEN_IN_RESPONSE", "0") or "0").strip() == "1"


# --- Logging --------------------------------------------------------------
def _safe_print(text):
    """print(), which a log line must never be able to fail at.

    stdout carries the console's encoding, and on Windows that is routinely
    cp1252 — where printing a character it cannot represent raises
    UnicodeEncodeError. That is a real failure mode here rather than a
    theoretical one: these lines quote email addresses, and an address with a
    Greek local part is entirely ordinary for this application. Unhandled, it
    would propagate out of the mailer, past the endpoint's neutral response,
    and answer 500 for a real address next to 200 for an unknown one — the
    exact enumeration oracle the whole flow is built to deny, introduced by
    the code that was supposed to explain a mail failure.

    So the line degrades to whatever the stream can represent rather than
    raising. Every string this module composes is ASCII for the same reason;
    this is the belt for the values interpolated into them, which are not.
    """
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"),
              flush=True)


def log(level, message):
    """One line on stdout, flushed. See the module docstring on flush=True."""
    _safe_print(f"[mail][{level}] {message}")


def _mask(secret):
    """Confirm a secret is present and how long it is, and nothing else.

    Length is genuinely diagnostic — a Resend key is `re_` plus ~30 chars, and
    "36 characters" versus "0 characters" versus "38 characters, starts with a
    quote" answers most credential questions — while the value itself is the
    one thing that must never reach a log.
    """
    if not secret:
        return "MISSING"
    note = f"set, {len(secret)} chars"
    if secret != secret.strip():
        note += ", HAS SURROUNDING WHITESPACE"
    if secret[:1] in ("'", '"') or secret[-1:] in ("'", '"'):
        note += ", LOOKS QUOTED (the quotes are probably part of the value)"
    return note


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


# --- What the failures mean -----------------------------------------------
#: Checked in order, so a subclass is matched before its base
#: (ConnectionRefusedError before ConnectionError before OSError).
_MEANINGS = (
    ((smtplib.SMTPAuthenticationError,),
     "the server REJECTED the credentials. Check SMTP_USER and SMTP_PASSWORD."),
    ((smtplib.SMTPSenderRefused,),
     "the server refused the FROM address. Usually the sending domain is not "
     "verified with the provider, or SMTP_FROM is not on a domain it owns."),
    ((smtplib.SMTPRecipientsRefused,),
     "the server refused every recipient. On a provider still in test/sandbox "
     "mode this is what sending to an unverified address looks like."),
    ((smtplib.SMTPNotSupportedError,),
     "the server does not offer something this code asked for - commonly "
     "STARTTLS on a port that expects implicit TLS, or AUTH before TLS."),
    ((smtplib.SMTPServerDisconnected,),
     "the server hung up mid-conversation. Usually the wrong TLS mode for the "
     "port: implicit TLS (465) spoken to a STARTTLS port, or the reverse."),
    ((smtplib.SMTPConnectError,),
     "the connection could not be established at all."),
    ((ssl.SSLError,),
     "the TLS handshake failed. Check SMTP_PORT against SMTP_STARTTLS: 587 "
     "wants STARTTLS on, 465 wants it off (implicit TLS)."),
    ((socket.gaierror,),
     "the host name did not resolve. Check SMTP_HOST for a typo."),
    ((TimeoutError, socket.timeout),
     "no answer before the timeout. Outbound SMTP is very often blocked by "
     "the host - try port 587 or 2587, or the provider's HTTPS API."),
    ((ConnectionRefusedError,),
     "the port refused the connection. Check SMTP_PORT."),
    ((ConnectionError,),
     "the connection dropped."),
    ((smtplib.SMTPResponseException,),
     "the server answered with an error code - see the response line above."),
)


def _meaning(exc):
    for types, sentence in _MEANINGS:
        if isinstance(exc, types):
            return sentence
    return None


def _as_text(value):
    """SMTP response lines arrive as bytes. Show them as text, losing nothing
    if they are not valid UTF-8 — the code and the wording are the point."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def _server_words(exc):
    """The fields carrying the server's OWN response, which is the whole
    reason to log an SMTP failure rather than just its type.

    str(exc) renders some of these for some exception classes and none of them
    for others, so they are pulled out explicitly instead of hoping the
    formatting happens to include the bit that matters.
    """
    lines = []
    code = getattr(exc, "smtp_code", None)
    if code is not None:
        lines.append(f"status code : {code}")
    error = getattr(exc, "smtp_error", None)
    if error is not None:
        lines.append(f"server said : {_as_text(error)}")
    sender = getattr(exc, "sender", None)
    if sender is not None:
        lines.append(f"refused FROM: {sender!r}")
    recipients = getattr(exc, "recipients", None)
    if recipients:
        for address, reply in dict(recipients).items():
            lines.append(f"refused TO  : {address!r} -> {_as_text(reply)}")
    reason = getattr(exc, "strerror", None)
    if reason:
        lines.append(f"os error    : {reason}")
    return lines


def _log_failure(exc, stage):
    """Everything known about a failed send, on stdout, in one block."""
    log("ERROR", f"password reset email FAILED at stage '{stage}'")
    log("ERROR", f"exception   : {type(exc).__module__}.{type(exc).__name__}")
    log("ERROR", f"message     : {exc}")
    for line in _server_words(exc):
        log("ERROR", line)
    meaning = _meaning(exc)
    if meaning:
        log("ERROR", f"likely cause: {meaning}")
    for hint in _provider_hints():
        log("ERROR", f"check       : {hint}")
    log("ERROR", "traceback follows:")
    print(traceback.format_exc(), flush=True)


# --- Provider-specific footguns -------------------------------------------
#: Resend's SMTP endpoint. Matched on to check the settings it is fussy about,
#: because every one of these fails as a bare "535" that names no cause.
_RESEND_HOST = "smtp.resend.com"
_RESEND_USER = "resend"
_RESEND_PORTS = (25, 465, 587, 2465, 2587)


def _provider_hints():
    """Configuration that is legal SMTP but wrong for the configured provider.

    Only checks what the server cannot tell you itself. Resend rejects the
    wrong username with the same 535 it uses for a wrong key, so "your username
    must be the literal string resend" is not something the response will ever
    say.
    """
    hints = []
    if _RESEND_HOST not in SMTP_HOST.lower():
        return hints
    if SMTP_USER != _RESEND_USER:
        hints.append(
            f"Resend requires SMTP_USER to be the literal string "
            f"{_RESEND_USER!r} - it is currently {SMTP_USER!r}. The API key "
            f"goes in SMTP_PASSWORD, not here.")
    if not SMTP_PASSWORD.startswith("re_"):
        hints.append(
            "Resend API keys start with 're_' - SMTP_PASSWORD does not look "
            "like one.")
    if SMTP_PORT not in _RESEND_PORTS:
        hints.append(
            f"Resend listens on {_RESEND_PORTS}; SMTP_PORT is {SMTP_PORT}.")
    if SMTP_PORT == 465 and SMTP_STARTTLS:
        hints.append("port 465 is implicit TLS - set SMTP_STARTTLS=0.")
    if "@" in SMTP_FROM:
        domain = SMTP_FROM.rsplit("@", 1)[-1].rstrip(">").strip()
        hints.append(
            f"the sending domain {domain!r} must be VERIFIED in the Resend "
            f"dashboard, and until it is every send is refused. Only "
            f"onboarding@resend.dev works unverified, and only to your own "
            f"account address.")
    return hints


def _log_configuration(to_email):
    """What this process is actually about to do, before it tries.

    Read from the environment at IMPORT, so this also answers the question
    that wastes the most time: whether the variables changed since the worker
    started. If a value here is not what the dashboard says, the process
    predates the change and needs a restart.
    """
    mode = ("implicit TLS (SMTPS)" if SMTP_PORT == 465
            else f"STARTTLS {'on' if SMTP_STARTTLS else 'OFF'}")
    log("INFO", f"sending password reset to {to_email}")
    log("INFO", f"host={SMTP_HOST!r} port={SMTP_PORT} {mode} "
                f"timeout={SMTP_TIMEOUT}s")
    log("INFO", f"user={SMTP_USER!r} password={_mask(SMTP_PASSWORD)}")
    log("INFO", f"from={SMTP_FROM!r}")
    if not SMTP_USER:
        log("WARN", "SMTP_USER is empty - connecting WITHOUT authentication. "
                    "Every hosted provider refuses that.")
    for hint in _provider_hints():
        log("WARN", hint)


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

    Which is why it narrates instead. Returning False is the only signal this
    function can give the caller, and the caller cannot act on it either, so
    the reason goes to stdout where an operator can read it.
    """
    if minutes is None:
        from server.store import RESET_TOKEN_TTL_MINUTES

        minutes = RESET_TOKEN_TTL_MINUTES
    body = _BODY.format(link=link, minutes=minutes)

    if not is_configured():
        # The visible stub. Logged so a developer (or an operator diagnosing a
        # "no email arrived" report) can complete the flow by hand.
        missing = [name for name, value in
                   (("SMTP_HOST", SMTP_HOST), ("SMTP_FROM", SMTP_FROM))
                   if not value]
        log("WARN", f"SMTP is not configured ({', '.join(missing)} unset) — "
                    f"password reset for {to_email} was NOT emailed.")
        log("WARN", f"link: {link}")
        return False

    _log_configuration(to_email)

    message = EmailMessage()
    message["Subject"] = _SUBJECT
    message["From"] = SMTP_FROM
    message["To"] = to_email
    message.set_content(body)

    # Named so a failure says WHERE it happened. "Failed at stage 'login'" and
    # "failed at stage 'connect'" send you to completely different settings,
    # and the exception type alone does not always distinguish them.
    stage = "connect"
    try:
        if SMTP_PORT == 465:
            smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT,
                                    context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT)
        with smtp:
            if SMTP_DEBUG:
                # Goes to stderr, which every log collector interleaves with
                # stdout. See the module docstring: this prints credentials.
                smtp.set_debuglevel(SMTP_DEBUG)
            log("INFO", f"connected to {SMTP_HOST}:{SMTP_PORT}")

            if SMTP_PORT != 465 and SMTP_STARTTLS:
                stage = "starttls"
                smtp.starttls(context=ssl.create_default_context())
                log("INFO", "STARTTLS negotiated")

            stage = "login"
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
                log("INFO", f"authenticated as {SMTP_USER!r}")

            stage = "send"
            refused = smtp.send_message(message)

        # send_message RETURNS the recipients that were refused when at least
        # one was accepted — it only raises when they all were. Without this
        # branch a partly-refused send is indistinguishable from a clean one,
        # and the address that did not get the mail is the one being reported.
        if refused:
            log("ERROR", f"the server accepted the message but REFUSED "
                         f"{len(refused)} recipient(s):")
            for address, reply in refused.items():
                log("ERROR", f"refused TO  : {address!r} -> {_as_text(reply)}")
            for hint in _provider_hints():
                log("ERROR", f"check       : {hint}")
            return False

        log("INFO", f"password reset email ACCEPTED by {SMTP_HOST} for "
                    f"{to_email}")
        return True
    except Exception as exc:
        # Logged in full, never surfaced — see the docstring.
        _log_failure(exc, stage)
        return False


# --- Standalone check -----------------------------------------------------
def diagnose(to_email):
    """Try one real send and report, without going near the database.

        python -m server.mailer you@example.com

    Exists because the endpoint is a bad place to test delivery from: it
    answers 200 whether or not the mail went, it needs a matching account, and
    it is rate limited. This runs the identical code path with the identical
    configuration and tells you what happened.
    """
    log("INFO", "--- SMTP diagnosis ---")
    if not is_configured():
        log("ERROR", "SMTP is not configured in this process. Set SMTP_HOST "
                     "(and SMTP_FROM) and try again.")
        return False
    sent = send_password_reset(to_email, reset_link("diagnostic-token"))
    log("INFO", f"--- result: {'SENT' if sent else 'NOT SENT'} ---")
    return sent


if __name__ == "__main__":   # pragma: no cover - operator tool
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m server.mailer <recipient@example.com>")
        raise SystemExit(2)
    raise SystemExit(0 if diagnose(sys.argv[1]) else 1)
