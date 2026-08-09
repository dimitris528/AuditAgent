"""
Outbound mail for the password-reset flow.

With neither transport configured `send_password_reset` LOGS the reset link at
WARN and returns False — a visible stub. The alternative, swallowing the send
and returning True, would leave the whole flow looking healthy while every user
waited forever for an email that was never going to arrive.

Two transports, and why
-----------------------
    RESEND_API_KEY set  ->  HTTPS POST to api.resend.com   (preferred)
    otherwise           ->  SMTP

SMTP came first and is still the fallback, because it is what every
transactional provider (SES, Postmark, Resend, Mailgun, plain Gmail) exposes,
so it works with all of them and needs no SDK.

The HTTPS path exists because that generality is worth nothing on a host that
will not let the connection out. Render's free and starter instances block
outbound SMTP — 25, 465, 587 and 2525 alike — and a blocked port does not
refuse, it hangs, so the symptom is a TimeoutError after the full timeout on
every attempt, with nothing at the provider end to look at. No SMTP setting
fixes that; the packets never leave. Port 443 does leave, and Resend's REST API
takes the same message over it.

So the API is preferred whenever a key is present, and SMTP is what runs when
one is not. A failure of the API does NOT then retry over SMTP: on the host
this exists for, that fallback is a guaranteed second timeout added to every
error, which is the opposite of the problem being solved.

Configuration
-------------
    RESEND_API_KEY      re_... — presence of this selects the HTTPS transport
    MAIL_FROM           the From address (falls back to RESEND_FROM, SMTP_FROM)
    MAIL_HTTP_TIMEOUT   seconds, default 15

    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (defaults to SMTP_USER), SMTP_STARTTLS (default on),
    SMTP_TIMEOUT (default 15), SMTP_DEBUG (default off — see below)

The HTTPS call is made with urllib from the standard library rather than the
`resend` SDK or `requests`. It is one POST with a JSON body, so an SDK earns
nothing; and `requests` is in this project solely for the Airtable backfill and
is marked for removal with it, so depending on it here would break mail on the
day that cleanup happens.

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

import json
import os
import smtplib
import socket
import ssl
import sys
import traceback
import urllib.error
import urllib.request
from email.message import EmailMessage

from config import APP_BASE_URL

SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "587").strip() or 587)
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_FROM = (os.getenv("SMTP_FROM") or SMTP_USER or "no-reply@localhost").strip()
SMTP_STARTTLS = (os.getenv("SMTP_STARTTLS", "1") or "1").strip() != "0"
SMTP_TIMEOUT = int((os.getenv("SMTP_TIMEOUT") or "15").strip() or 15)

# --- Resend HTTPS API -----------------------------------------------------
#: Presence of this selects the HTTPS transport. See the module docstring.
RESEND_API_KEY = (os.getenv("RESEND_API_KEY") or "").strip()

#: Overridable only so a test can point it somewhere that is not the internet.
RESEND_API_URL = ((os.getenv("RESEND_API_URL") or "").strip()
                  or "https://api.resend.com/emails")

#: The From address, for whichever transport runs. SMTP_FROM is the fallback so
#: a deployment that already had SMTP working keeps its sender when it switches
#: to the API and sets nothing else.
MAIL_FROM = ((os.getenv("MAIL_FROM") or "").strip()
             or (os.getenv("RESEND_FROM") or "").strip()
             or SMTP_FROM)

MAIL_HTTP_TIMEOUT = int((os.getenv("MAIL_HTTP_TIMEOUT") or "15").strip() or 15)

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


#: The two transports, by the name that appears in the log.
TRANSPORT_API = "resend-api"
TRANSPORT_SMTP = "smtp"


def transport():
    """Which way mail leaves this process, or None if it cannot.

    An API key WINS over SMTP whenever both are set, rather than being a last
    resort: the deployment that has both is the one that configured SMTP,
    watched it time out, and added a key — so the key is the newer answer and
    the SMTP variables are the thing left behind.
    """
    if RESEND_API_KEY:
        return TRANSPORT_API
    if SMTP_HOST and MAIL_FROM:
        return TRANSPORT_SMTP
    return None


def is_configured():
    """True when a real send is possible, by either transport."""
    return transport() is not None


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
     "verified with the provider, or MAIL_FROM is not on a domain it owns."),
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
    for hint in _provider_hints(failing=True):
        log("ERROR", f"check       : {hint}")
    log("ERROR", "traceback follows:")
    print(traceback.format_exc(), flush=True)


# --- Provider-specific footguns -------------------------------------------
#: Resend's SMTP endpoint. Matched on to check the settings it is fussy about,
#: because every one of these fails as a bare "535" that names no cause.
_RESEND_HOST = "smtp.resend.com"
_RESEND_USER = "resend"
_RESEND_PORTS = (25, 465, 587, 2465, 2587)


# Two kinds of advice, and the difference decides where each is logged.
#
# A PROBLEM is definitely wrong and is worth saying before the attempt: the
# From address is not an address, the key is not shaped like a key. A REMINDER
# is merely a common cause and cannot be checked from here — whether a domain
# is verified is a fact in someone's dashboard, not in this process.
#
# Reminders are logged only on FAILURE. Logged up front they would print on
# every successful password reset of a correctly configured deployment, and a
# warning that is always there is one nobody reads on the day it matters.
def _sender_problem():
    """A From address that is definitely wrong, rather than merely unproven."""
    if "@" not in MAIL_FROM:
        return (f"the From address {MAIL_FROM!r} is not an email address. Set "
                f"MAIL_FROM to one on a domain verified with the provider.")
    domain = MAIL_FROM.rsplit("@", 1)[-1].rstrip(">").strip().lower()
    if domain in ("localhost", "localhost.localdomain"):
        return (f"the From address is still the default {MAIL_FROM!r}, which "
                f"no provider will accept. Set MAIL_FROM.")
    return None


def _verification_reminder():
    """The commonest cause of a refused Resend send, and one this process
    cannot verify for itself."""
    if "@" not in MAIL_FROM:
        return None
    domain = MAIL_FROM.rsplit("@", 1)[-1].rstrip(">").strip().lower()
    if domain in ("resend.dev", "localhost", "localhost.localdomain"):
        return None
    return (f"the sending domain {domain!r} must be VERIFIED in the Resend "
            f"dashboard, and until it is every send is refused. Only "
            f"onboarding@resend.dev works unverified, and only to your own "
            f"account address.")


def _api_hints(failing=False):
    """Configuration wrong for the HTTPS transport."""
    hints = []
    if not RESEND_API_KEY.startswith("re_"):
        hints.append("Resend API keys start with 're_' - RESEND_API_KEY does "
                     "not look like one.")
    problem = _sender_problem()
    if problem:
        hints.append(problem)
    if failing:
        reminder = _verification_reminder()
        if reminder:
            hints.append(reminder)
    return hints


def _provider_hints(failing=False):
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
    problem = _sender_problem()
    if problem:
        hints.append(problem)
    if failing:
        reminder = _verification_reminder()
        if reminder:
            hints.append(reminder)
        hints.append(
            "if this is a timeout rather than a rejection, the port is blocked "
            "and no SMTP setting will help. Set RESEND_API_KEY to send the "
            "same message over HTTPS instead.")
    return hints


def _log_configuration(to_email, chosen):
    """What this process is actually about to do, before it tries.

    Read from the environment at IMPORT, so this also answers the question
    that wastes the most time: whether the variables changed since the worker
    started. If a value here is not what the dashboard says, the process
    predates the change and needs a restart.
    """
    log("INFO", f"sending password reset to {to_email} via {chosen}")
    log("INFO", f"from={MAIL_FROM!r}")

    if chosen == TRANSPORT_API:
        log("INFO", f"endpoint={RESEND_API_URL} timeout={MAIL_HTTP_TIMEOUT}s")
        log("INFO", f"api key={_mask(RESEND_API_KEY)}")
        if SMTP_HOST:
            log("INFO", f"(SMTP_HOST={SMTP_HOST!r} is set but UNUSED - the API "
                        f"key takes precedence)")
        for hint in _api_hints():
            log("WARN", hint)
        return

    mode = ("implicit TLS (SMTPS)" if SMTP_PORT == 465
            else f"STARTTLS {'on' if SMTP_STARTTLS else 'OFF'}")
    log("INFO", f"host={SMTP_HOST!r} port={SMTP_PORT} {mode} "
                f"timeout={SMTP_TIMEOUT}s")
    log("INFO", f"user={SMTP_USER!r} password={_mask(SMTP_PASSWORD)}")
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
        log("WARN", "no mail transport is configured (set RESEND_API_KEY, or "
                    "SMTP_HOST and MAIL_FROM) - password reset for "
                    f"{to_email} was NOT emailed.")
        log("WARN", f"link: {link}")
        return False

    # transport() rather than the RESEND_API_KEY check inline: is_configured is
    # monkeypatched in tests, and reading the choice from one function keeps
    # "which transport" answerable in exactly one place.
    chosen = transport() or TRANSPORT_SMTP
    _log_configuration(to_email, chosen)

    if chosen == TRANSPORT_API:
        return _send_via_api(to_email, _SUBJECT, body)

    message = EmailMessage()
    message["Subject"] = _SUBJECT
    message["From"] = MAIL_FROM
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
            for hint in _provider_hints(failing=True):
                log("ERROR", f"check       : {hint}")
            return False

        log("INFO", f"password reset email ACCEPTED by {SMTP_HOST} for "
                    f"{to_email}")
        return True
    except Exception as exc:
        # Logged in full, never surfaced — see the docstring.
        _log_failure(exc, stage)
        return False


# --- The HTTPS transport --------------------------------------------------
#: What each status from api.resend.com actually means. The response body
#: usually says so too, but not always, and a status with no body at all is
#: exactly the case where a fixed sentence earns its place.
_API_STATUS_MEANINGS = {
    401: "the API key was rejected. Check RESEND_API_KEY.",
    403: "the request was refused - almost always a sending domain that is "
         "not verified, or a From address on a domain this key cannot use.",
    404: "the endpoint was not found. Check RESEND_API_URL.",
    422: "the payload was rejected as invalid - usually the From or To "
         "address.",
    429: "rate limited. Resend allows a limited number of requests per "
         "second on the free plan.",
}

#: A response body is a diagnostic, not a document. Enough to carry Resend's
#: message and name; not enough to bury the log if something returns a page.
_MAX_BODY_CHARS = 2000


def _log_api_body(level, raw):
    """The API's own answer, parsed when it is JSON and quoted when it is not.

    Resend's errors arrive as {"statusCode":..., "message":..., "name":...},
    and `message` is the sentence worth reading — it names the unverified
    domain, or the malformed address, in words. Falling back to the raw text
    matters just as much: a proxy or a WAF between here and Resend answers
    with HTML, and "the body was not JSON" is itself the finding.
    """
    if not raw:
        log(level, "response body: <empty>")
        return
    try:
        parsed = json.loads(raw)
    except ValueError:
        log(level, f"response body (not JSON): {raw[:_MAX_BODY_CHARS]}")
        return
    if isinstance(parsed, dict):
        for field in ("name", "message", "error", "id"):
            if field in parsed:
                log(level, f"{field:12}: {parsed[field]}")
        unknown = set(parsed) - {"name", "message", "error", "id", "statusCode"}
        if unknown:
            log(level, f"other fields: {sorted(unknown)}")
        return
    log(level, f"response body: {str(parsed)[:_MAX_BODY_CHARS]}")


def _send_via_api(to_email, subject, body):
    """POST the message to Resend over HTTPS. Never raises; returns True when
    Resend accepted it.

    Port 443, which is the entire point — see the module docstring on why the
    SMTP path cannot work on a host that blocks outbound mail ports.
    """
    payload = json.dumps({
        "from": MAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }).encode("utf-8")

    request = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=MAIL_HTTP_TIMEOUT) as res:
            raw = res.read().decode("utf-8", "replace")
            log("INFO", f"HTTP {res.status} from the Resend API")
            _log_api_body("INFO", raw)
            log("INFO", f"password reset email ACCEPTED by Resend for "
                        f"{to_email}")
            return True
    except urllib.error.HTTPError as exc:
        # A non-2xx. The body is the provider's own explanation and is the
        # single most useful thing in this module's output, so it is read
        # before anything else can close the connection.
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:                       # pragma: no cover - defensive
            raw = ""
        log("ERROR", f"password reset email REFUSED by the Resend API: "
                     f"HTTP {exc.code} {exc.reason}")
        _log_api_body("ERROR", raw)
        meaning = _API_STATUS_MEANINGS.get(exc.code)
        if meaning:
            log("ERROR", f"likely cause: {meaning}")
        for hint in _api_hints(failing=True):
            log("ERROR", f"check       : {hint}")
        return False
    except urllib.error.URLError as exc:
        # DNS, TLS, or the connection itself. `reason` is the wrapped
        # exception, which is where the real cause lives.
        reason = getattr(exc, "reason", exc)
        log("ERROR", "password reset email FAILED before the Resend API "
                     "answered")
        log("ERROR", f"exception   : {type(reason).__module__}."
                     f"{type(reason).__name__}")
        log("ERROR", f"message     : {reason}")
        if isinstance(reason, (TimeoutError, socket.timeout)):
            log("ERROR", "likely cause: HTTPS to api.resend.com timed out. "
                         "Unlike SMTP this is not normally blocked - suspect "
                         "an outbound proxy or a DNS problem.")
        elif isinstance(reason, ssl.SSLError):
            log("ERROR", "likely cause: TLS failed. A proxy intercepting "
                         "HTTPS without its CA installed does this.")
        elif isinstance(reason, socket.gaierror):
            log("ERROR", "likely cause: api.resend.com did not resolve. "
                         "Check RESEND_API_URL and the host's DNS.")
        log("ERROR", "traceback follows:")
        _safe_print(traceback.format_exc())
        return False
    except Exception as exc:
        log("ERROR", "password reset email FAILED in the Resend API client")
        log("ERROR", f"exception   : {type(exc).__module__}."
                     f"{type(exc).__name__}")
        log("ERROR", f"message     : {exc}")
        log("ERROR", "traceback follows:")
        _safe_print(traceback.format_exc())
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
    log("INFO", "--- mail diagnosis ---")
    chosen = transport()
    if chosen is None:
        log("ERROR", "no mail transport is configured in this process. Set "
                     "RESEND_API_KEY (preferred), or SMTP_HOST and MAIL_FROM.")
        return False
    log("INFO", f"transport: {chosen}")
    sent = send_password_reset(to_email, reset_link("diagnostic-token"))
    log("INFO", f"--- result: {'SENT' if sent else 'NOT SENT'} ---")
    return sent


if __name__ == "__main__":   # pragma: no cover - operator tool
    if len(sys.argv) != 2:
        print("usage: python -m server.mailer <recipient@example.com>")
        raise SystemExit(2)
    raise SystemExit(0 if diagnose(sys.argv[1]) else 1)
