"""
Invoice OCR — structured extraction from a scanned document via OpenAI Vision.

One Chat Completions call per document: the file goes up as an `image_url`
content part carrying a base64 data URI (JPG/PNG/WEBP/GIF, including a camera
capture) or as a `file` part (PDF), and the reply is constrained by Structured
Outputs to a JSON schema — so the endpoint parses fields rather than prose.

The model is asked ONLY to read what is printed on the page. Every DERIVED
figure — the VAT rate, the net amount, the ΑΦΜ with its formatting stripped —
is recomputed here from the euros it returned, because arithmetic is the one
thing not to delegate to a language model in an accounting book. Nothing is
saved either: the extraction is handed to the UI to review, and the user
confirms it through the ordinary transaction form.

Configuration: OPENAI_API_KEY, and OPENAI_MODEL for the model id. Without a key
the endpoint reports 503 rather than pretending to scan — a silently empty
extraction reads as a blank invoice and would be filed as one.

Two model requirements, both of which fail on the FIRST REQUEST rather than at
startup, so they are worth stating plainly:
  * vision — the document is sent as an image;
  * Structured Outputs (`response_format` with a json_schema and strict:true) —
    without it the reply is prose and _normalise has nothing to read.
Sending a PDF additionally needs a model that accepts file inputs; a deployment
whose model does not can still scan photos, and _openai_error surfaces the
refusal instead of masking it.
"""

import base64
import datetime as dt
import json
import re

import finance
from config import OPENAI_API_KEY, OPENAI_MODEL
from server.text import afm_key

# What a phone camera and a scanner actually produce. PDFs ride the `file`
# content part; everything else is an image part.
PDF_TYPE = "application/pdf"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
SUPPORTED_TYPES = (PDF_TYPE,) + IMAGE_TYPES

# Base64 inflates a payload by ~4/3, so 12 MB of file stays comfortably inside
# the request limit while still accepting a full-resolution phone photo.
MAX_BYTES = 12 * 1024 * 1024

# A VAT rate derived from the printed euros never lands exactly on 0.24; snap
# it to the statutory rate it is nearest, and only if it is genuinely close.
_RATE_TOLERANCE = 0.015


class OcrError(RuntimeError):
    """Extraction failed. Message is user-facing Greek."""

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def is_configured():
    """True when a key is present. Checked before the upload is even read, so
    an unconfigured deployment fails fast instead of after a 10 MB POST."""
    return bool(OPENAI_API_KEY)


def _nullable(*types):
    """A schema fragment that accepts the given JSON types or null.

    Written as a type UNION (`"type": ["string", "null"]`) rather than an
    `anyOf`, because that is the form OpenAI Structured Outputs accepts for an
    optional field under `strict: true`.

    Every field is nullable on purpose: a real invoice routinely omits one of
    them, and forcing the model to produce a value is how you get an invented
    one. Strict mode also requires EVERY property to appear in `required`, so
    "optional" has to be expressed in the type rather than by omission.
    """
    return {"type": list(types) + ["null"]}


_FIELDS = {
    "total_amount": _nullable("number"),
    "vat_amount": _nullable("number"),
    "net_amount": _nullable("number"),
    "doc_date": _nullable("string"),
    "doc_number": _nullable("string"),
    "issuer_name": _nullable("string"),
    "issuer_afm": _nullable("string"),
    "recipient_name": _nullable("string"),
    "recipient_afm": _nullable("string"),
    "currency": _nullable("string"),
    "document_type": _nullable("string"),
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "notes": _nullable("string"),
}

_SCHEMA = {
    "type": "object",
    "properties": _FIELDS,
    "required": sorted(_FIELDS),
    "additionalProperties": False,
}

_SYSTEM = (
    "Είσαι βοηθός λογιστηρίου που διαβάζει ελληνικά παραστατικά (τιμολόγια, "
    "αποδείξεις, δελτία παροχής υπηρεσιών). Εξάγεις ΜΟΝΟ ό,τι είναι τυπωμένο "
    "στο έγγραφο.\n"
    "Κανόνες:\n"
    "- Μην υπολογίζεις και μην μαντεύεις τιμές. Αν κάτι δεν φαίνεται καθαρά, "
    "επίστρεψε null.\n"
    "- total_amount είναι το ΤΕΛΙΚΟ πληρωτέο ποσό με ΦΠΑ, net_amount η καθαρή "
    "αξία χωρίς ΦΠΑ, vat_amount το ποσό του ΦΠΑ. Όλα θετικοί αριθμοί, με "
    "τελεία ως υποδιαστολή.\n"
    "- Στα ελληνικά παραστατικά τα ποσά γράφονται 1.234,56 — αυτό είναι "
    "1234.56.\n"
    "- doc_date σε μορφή YYYY-MM-DD. Οι ελληνικές ημερομηνίες είναι "
    "ΗΗ/ΜΜ/ΕΕΕΕ.\n"
    "- issuer είναι ο ΕΚΔΟΤΗΣ του παραστατικού (ο προμηθευτής), recipient ο "
    "παραλήπτης/πελάτης.\n"
    "- Το ΑΦΜ είναι 9 ψηφία. Μην το μπερδεύεις με ΓΕΜΗ, ΔΟΥ ή αριθμό "
    "τηλεφώνου.\n"
    "- confidence: high αν το έγγραφο είναι ευανάγνωστο και τα βασικά πεδία "
    "βρέθηκαν, low αν είναι θολό ή κομμένο."
)

_PROMPT = (
    "Διάβασε αυτό το παραστατικό και επίστρεψε τα στοιχεία του. "
    "Ό,τι δεν διαβάζεται καθαρά, άφησέ το null."
)


def _client():
    """Build the SDK client lazily.

    Imported inside the function so the module — and therefore the whole app —
    still imports on a deployment that never installed the SDK; the endpoint
    reports it instead of the process failing to boot.
    """
    if not OPENAI_API_KEY:
        raise OcrError(
            "Η σάρωση παραστατικών δεν έχει ρυθμιστεί (ορίστε OPENAI_API_KEY).",
            status=503)
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - deployment problem
        raise OcrError(
            "Λείπει η βιβλιοθήκη openai στον server.", status=503) from exc
    return openai, openai.OpenAI(api_key=OPENAI_API_KEY)


def _data_uri(data, media_type):
    """base64 data URI — how both content parts carry the bytes inline.

    Inline rather than through the Files API on purpose: an upload would need a
    second round trip and would leave the customer's invoice sitting in
    OpenAI's file storage afterwards, which is a retention decision this
    endpoint has no business making on its own.
    """
    encoded = base64.standard_b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _content_block(data, media_type, filename=None):
    """The image/file content part for one uploaded document."""
    uri = _data_uri(data, media_type)
    if media_type == PDF_TYPE:
        return {"type": "file",
                # The filename is required by the API for a file part and is
                # only a label; the bytes are what get read.
                "file": {"filename": filename or "document.pdf",
                         "file_data": uri}}
    # "high" detail: an invoice is small type, and the low-detail path
    # downsamples enough to lose a document number.
    return {"type": "image_url", "image_url": {"url": uri, "detail": "high"}}


def extract(data, media_type, filename=None):
    """Read one document and return the reviewed-by-the-user field set.

    Raises OcrError (with an HTTP status) for anything the caller should show
    rather than log.
    """
    media_type = (media_type or "").split(";")[0].strip().lower()
    if media_type not in SUPPORTED_TYPES:
        raise OcrError(
            "Μη υποστηριζόμενος τύπος αρχείου. Στείλτε PDF, JPG ή PNG.",
            status=415)
    if not data:
        raise OcrError("Το αρχείο είναι κενό.", status=422)
    if len(data) > MAX_BYTES:
        raise OcrError(
            f"Το αρχείο ξεπερνά το όριο των {MAX_BYTES // (1024 * 1024)} MB.",
            status=413)

    openai, client = _client()
    try:
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_completion_tokens=4000,
            # Deterministic as the API allows: the same invoice scanned twice
            # should not produce two different document numbers.
            temperature=0,
            # Structured Outputs. `strict` is what makes the schema a
            # GUARANTEE rather than a request, so the parse below cannot meet
            # a stray "Here is the invoice:" preamble.
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "greek_invoice", "strict": True,
                                "schema": _SCHEMA},
            },
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",
                 "content": [_content_block(data, media_type, filename),
                             {"type": "text", "text": _PROMPT}]},
            ],
        )
    except openai.AuthenticationError as exc:
        raise OcrError("Το OPENAI_API_KEY δεν έγινε δεκτό.", status=502) from exc
    except openai.RateLimitError as exc:
        raise OcrError(
            "Υπέρβαση ορίου κλήσεων στη σάρωση. Δοκιμάστε ξανά σε λίγο.",
            status=429) from exc
    except openai.BadRequestError as exc:
        # Overwhelmingly a model that cannot do what this endpoint needs —
        # no vision, no Structured Outputs, or no PDF input. Named explicitly
        # because the generic "service error" below sends people looking at
        # their network instead of at OPENAI_MODEL.
        print(f"[ERROR] OpenAI rejected the scan request (model={OPENAI_MODEL}): {exc}")
        raise OcrError(
            "Το μοντέλο σάρωσης δεν δέχτηκε το έγγραφο. Ελέγξτε το OPENAI_MODEL "
            "(χρειάζεται μοντέλο με υποστήριξη εικόνας).", status=502) from exc
    except openai.APIStatusError as exc:
        raise OcrError(
            f"Η υπηρεσία σάρωσης απάντησε με σφάλμα ({exc.status_code}).",
            status=502) from exc
    except openai.APIConnectionError as exc:
        raise OcrError("Δεν ήταν δυνατή η σύνδεση με την υπηρεσία σάρωσης.",
                       status=502) from exc

    choice = completion.choices[0]
    message = choice.message

    # A Structured Outputs refusal comes back as HTTP 200 with `refusal` set
    # and `content` empty, so it has to be checked BEFORE the content is read
    # or the parse fails with a misleading "unreadable document".
    if getattr(message, "refusal", None):
        raise OcrError("Η σάρωση του εγγράφου απορρίφθηκε.", status=422)
    # A truncated reply is invalid JSON, and reporting it as unreadable would
    # send someone re-photographing a document that scanned perfectly well.
    if choice.finish_reason == "length":
        raise OcrError(
            "Το έγγραφο ήταν πολύ μεγάλο για να διαβαστεί σε μία σάρωση.",
            status=422)

    try:
        parsed = json.loads(message.content)
    except (TypeError, ValueError) as exc:
        raise OcrError("Δεν ήταν δυνατή η ανάγνωση του παραστατικού.",
                       status=502) from exc

    return _normalise(parsed, filename=filename)


# --------------------------------------------------------------------------
# Post-processing — everything below is arithmetic, done here and not by the
# model.
# --------------------------------------------------------------------------
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DMY = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")


def _amount(value):
    """A positive euro figure, or None.

    Magnitude only: a credit note prints its total unsigned, and the sign in
    this book comes from the transaction TYPE the user picks, never from the
    document.
    """
    if value is None:
        return None
    try:
        number = round(abs(float(value)), 2)
    except (TypeError, ValueError):
        return None
    return number or None


def _date(value):
    """ISO date string, accepting the Greek ΗΗ/ΜΜ/ΕΕΕΕ form as well."""
    if not value:
        return None
    text = str(value).strip()
    iso = _ISO.match(text)
    if iso:
        year, month, day = (int(g) for g in iso.groups())
    else:
        dmy = _DMY.match(text)
        if not dmy:
            return None
        day, month, year = (int(g) for g in dmy.groups())
        if year < 100:
            year += 2000
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _vat_rate(total, vat, net):
    """The rate the document was taxed at, snapped to a statutory one.

    Derived from the printed euros rather than read off the page: an invoice
    prints "ΦΠΑ 24%" next to a figure that may cover several lines at
    different rates, and the figure is what has to reconcile.
    """
    base = net
    if base is None and total is not None and vat is not None:
        base = round(total - vat, 2)
    if not base or vat is None or base <= 0:
        return None
    derived = vat / base
    nearest = min(finance.VAT_RATES, key=lambda r: abs(r - derived))
    return nearest if abs(nearest - derived) <= _RATE_TOLERANCE else None


def _clean(value, limit):
    if value is None:
        return None
    text = " ".join(str(value).split())[:limit]
    return text or None


def _normalise(parsed, filename=None):
    """Turn the model's answer into the payload the transaction form consumes."""
    total = _amount(parsed.get("total_amount"))
    vat = _amount(parsed.get("vat_amount"))
    net = _amount(parsed.get("net_amount"))

    # Fill in whichever of the three the document did not print. Only ever from
    # the other two — never from an assumed rate.
    if total is None and net is not None and vat is not None:
        total = round(net + vat, 2)
    if net is None and total is not None and vat is not None:
        net = round(total - vat, 2)
    if vat is None and total is not None and net is not None:
        vat = round(total - net, 2)

    # A "VAT" larger than the total is a misread, not a discount; drop it
    # rather than let it through into the form.
    if vat is not None and total is not None and vat >= total:
        vat = None
        net = None

    issuer_afm = afm_key(parsed.get("issuer_afm")) or None
    recipient_afm = afm_key(parsed.get("recipient_afm")) or None

    return {
        "total_amount": total,
        "vat_amount": vat,
        "net_amount": net,
        "vat_rate": _vat_rate(total, vat, net),
        "doc_date": _date(parsed.get("doc_date")),
        "doc_number": _clean(parsed.get("doc_number"), 64),
        # The counterparty is the ISSUER: a scanned document is overwhelmingly
        # a supplier's invoice, and that is the party the row is filed against.
        # The recipient rides along so the UI can show who it was made out to
        # and the user can flip them on a sales invoice.
        "counterparty_name": _clean(parsed.get("issuer_name"), 200),
        "counterparty_afm": issuer_afm,
        "recipient_name": _clean(parsed.get("recipient_name"), 200),
        "recipient_afm": recipient_afm,
        "currency": _clean(parsed.get("currency"), 8),
        "document_type": _clean(parsed.get("document_type"), 64),
        "confidence": parsed.get("confidence") or "low",
        "notes": _clean(parsed.get("notes"), 500),
        "filename": _clean(filename, 200),
    }
