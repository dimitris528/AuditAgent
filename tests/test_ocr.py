"""
Invoice OCR — the arithmetic done AFTER the model answers, the request the
OpenAI backend builds, and the endpoint's unconfigured path.

Nothing here reaches the network. Two things are worth testing and neither is
the model itself: the derivations and guards that keep a misread out of the
books, and the SHAPE of the request — which is the part a provider migration
can silently get wrong, because a malformed content part fails at OpenAI, in
production, on someone's real invoice.
"""

import io
import json

import pytest

from server import ocr


def _extract(**fields):
    """Run the post-processing over one model answer."""
    answer = {key: None for key in ocr._FIELDS}
    answer["confidence"] = "high"
    answer.update(fields)
    return ocr._normalise(answer)


# --- Amounts --------------------------------------------------------------
def test_missing_third_figure_is_derived_from_the_other_two():
    assert _extract(total_amount=124, vat_amount=24)["net_amount"] == 100.0
    assert _extract(net_amount=100, vat_amount=24)["total_amount"] == 124.0
    assert _extract(total_amount=124, net_amount=100)["vat_amount"] == 24.0


def test_a_lone_total_is_left_alone():
    """No rate is assumed: the form's default applies, visibly, where the user
    can change it."""
    out = _extract(total_amount=124)
    assert out["total_amount"] == 124.0
    assert out["vat_amount"] is None
    assert out["vat_rate"] is None


def test_amounts_come_back_as_positive_magnitudes():
    """A credit note prints its total unsigned; the sign in this book comes
    from the transaction type the user picks."""
    assert _extract(total_amount=-124, vat_amount=-24)["total_amount"] == 124.0


def test_vat_larger_than_the_total_is_dropped_as_a_misread():
    out = _extract(total_amount=100, vat_amount=124)
    assert out["vat_amount"] is None
    assert out["net_amount"] is None
    assert out["total_amount"] == 100.0


# --- VAT rate -------------------------------------------------------------
@pytest.mark.parametrize("total,vat,expected", [
    (124.0, 24.0, 0.24),
    (113.0, 13.0, 0.13),
    (106.0, 6.0, 0.06),
    # Real invoices round per line, so the derived rate never lands exactly on
    # the statutory one.
    (1234.56, 238.94, 0.24),
])
def test_rate_is_derived_and_snapped_to_a_statutory_one(total, vat, expected):
    assert _extract(total_amount=total, vat_amount=vat)["vat_rate"] == expected


def test_a_rate_that_matches_nothing_stays_unset():
    """Better an empty field the user fills than a confident wrong one."""
    assert _extract(total_amount=100, vat_amount=18)["vat_rate"] is None


# --- Dates ----------------------------------------------------------------
@pytest.mark.parametrize("written", [
    "2026-07-14", "14/07/2026", "14-07-2026", "14.07.2026", "14/7/26",
])
def test_greek_and_iso_dates_both_normalise(written):
    assert _extract(doc_date=written)["doc_date"] == "2026-07-14"


@pytest.mark.parametrize("written", ["", "άγνωστη", "2026-13-45", "31/02/2026"])
def test_an_unreadable_date_is_dropped(written):
    assert _extract(doc_date=written)["doc_date"] is None


# --- Identity -------------------------------------------------------------
def test_the_issuer_becomes_the_counterparty():
    out = _extract(issuer_name="Προμηθευτής ΑΕ", issuer_afm="EL 123 456 789",
                   recipient_name="Νησίδα Café", recipient_afm="987654321")
    assert out["counterparty_name"] == "Προμηθευτής ΑΕ"
    assert out["counterparty_afm"] == "123456789"
    # The recipient rides along so the UI can offer to swap them on a sales
    # invoice.
    assert out["recipient_name"] == "Νησίδα Café"
    assert out["recipient_afm"] == "987654321"


def test_whitespace_and_length_are_tidied():
    out = _extract(doc_number="  ΤΠΥ\n-1042 ", issuer_name="Α" * 400)
    assert out["doc_number"] == "ΤΠΥ -1042"
    assert len(out["counterparty_name"]) == 200


# --- Input validation -----------------------------------------------------
def test_unsupported_file_types_are_refused_before_any_api_call():
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"MZ\x90\x00", "application/x-msdownload", "invoice.exe")
    assert err.value.status == 415


def test_an_empty_upload_is_refused():
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"", "application/pdf", "empty.pdf")
    assert err.value.status == 422


def test_an_oversized_upload_is_refused():
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"x" * (ocr.MAX_BYTES + 1), "image/jpeg", "huge.jpg")
    assert err.value.status == 413


def test_the_camera_and_scanner_content_types_are_all_accepted():
    assert "application/pdf" in ocr.SUPPORTED_TYPES
    for image in ("image/jpeg", "image/png", "image/webp"):
        assert image in ocr.SUPPORTED_TYPES


# --- The OpenAI request ---------------------------------------------------
# The scan is one Chat Completions call. These cover what that call has to look
# like; a stub stands in for the network so the assertions are about our
# request, not about OpenAI's behaviour.
class _FakeMessage:
    def __init__(self, content, refusal=None):
        self.content = content
        self.refusal = refusal


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, choice):
        self.choices = [choice]


@pytest.fixture()
def openai_stub(monkeypatch):
    """Configure a fake key and capture the request `extract` sends."""
    import openai

    monkeypatch.setattr(ocr, "OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(ocr, "OPENAI_MODEL", "gpt-4o")
    sent = {}
    reply = {"content": json.dumps({
        "total_amount": 124, "vat_amount": 24, "net_amount": 100,
        "doc_date": "14/07/2026", "doc_number": "ΤΠΥ-1042",
        "issuer_name": "Προμηθευτής ΑΕ", "issuer_afm": "123456789",
        "recipient_name": None, "recipient_afm": None,
        "currency": "EUR", "document_type": "Τιμολόγιο",
        "confidence": "high", "notes": None,
    }), "refusal": None, "finish_reason": "stop"}

    class _Completions:
        @staticmethod
        def create(**params):
            sent.update(params)
            return _FakeCompletion(_FakeChoice(
                _FakeMessage(reply["content"], reply["refusal"]),
                reply["finish_reason"]))

    class _FakeClient:
        chat = type("chat", (), {"completions": _Completions})()

    monkeypatch.setattr(ocr, "_client",
                        lambda: (openai, _FakeClient()))
    # `reply` is handed back so a test can rewrite what the model "returned".
    sent["_reply"] = reply
    return sent


def test_a_photo_is_sent_as_an_image_part(openai_stub):
    out = ocr.extract(b"\xff\xd8\xff\xe0jpegbytes", "image/jpeg", "receipt.jpg")
    part = openai_stub["messages"][1]["content"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # Small type on an invoice: low detail downsamples away document numbers.
    assert part["image_url"]["detail"] == "high"
    assert out["doc_number"] == "ΤΠΥ-1042"


def test_a_pdf_is_sent_as_a_file_part(openai_stub):
    ocr.extract(b"%PDF-1.4 bytes", "application/pdf", "invoice.pdf")
    part = openai_stub["messages"][1]["content"][0]
    assert part["type"] == "file"
    assert part["file"]["filename"] == "invoice.pdf"
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_the_reply_is_pinned_to_the_schema(openai_stub):
    """Structured Outputs with strict:true is what makes the schema a guarantee
    — without it the reply can carry a prose preamble and the parse breaks."""
    ocr.extract(b"jpegbytes", "image/jpeg", "receipt.jpg")
    fmt = openai_stub["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is ocr._SCHEMA
    assert openai_stub["model"] == "gpt-4o"
    # The same invoice scanned twice must not yield two document numbers.
    assert openai_stub["temperature"] == 0


def test_the_schema_satisfies_structured_outputs_strict_mode(openai_stub):
    """Strict mode rejects a schema that allows unlisted keys or omits any
    property from `required` — and it rejects it at request time, on a real
    invoice, so it is checked here instead."""
    schema = ocr._SCHEMA
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])
    # Optional fields are expressed as a nullable TYPE UNION, which is the form
    # strict mode accepts — not by leaving them out of `required`.
    assert schema["properties"]["doc_number"]["type"] == ["string", "null"]


def test_a_refusal_is_reported_rather_than_parsed(openai_stub):
    openai_stub["_reply"]["content"] = None
    openai_stub["_reply"]["refusal"] = "I cannot help with that."
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"jpegbytes", "image/jpeg", "x.jpg")
    assert err.value.status == 422


def test_a_truncated_reply_is_not_reported_as_an_unreadable_document(openai_stub):
    """Saying "unreadable" here sends someone re-photographing a document that
    scanned perfectly well."""
    openai_stub["_reply"]["finish_reason"] = "length"
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"jpegbytes", "image/jpeg", "x.jpg")
    assert err.value.status == 422
    assert "μεγάλο" in str(err.value)


def test_unparseable_json_surfaces_as_a_read_failure(openai_stub):
    openai_stub["_reply"]["content"] = "not json at all"
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"jpegbytes", "image/jpeg", "x.jpg")
    assert err.value.status == 502


def test_a_model_that_cannot_do_vision_says_so(monkeypatch):
    """A BadRequest here is almost always OPENAI_MODEL, and a generic "service
    error" sends people looking at their network instead."""
    import httpx
    import openai

    monkeypatch.setattr(ocr, "OPENAI_API_KEY", "sk-test-not-real")
    # The SDK's exceptions read `response.request`, so a real one is needed.
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.openai.com/v1/x"))

    class _Completions:
        @staticmethod
        def create(**_params):
            raise openai.BadRequestError(
                "unsupported content part", response=response, body=None)

    class _FakeClient:
        chat = type("chat", (), {"completions": _Completions})()

    monkeypatch.setattr(ocr, "_client", lambda: (openai, _FakeClient()))
    with pytest.raises(ocr.OcrError) as err:
        ocr.extract(b"jpegbytes", "image/jpeg", "x.jpg")
    assert "OPENAI_MODEL" in str(err.value)


# --- Endpoint -------------------------------------------------------------
def test_scan_endpoint_reports_when_no_key_is_configured(api):
    """The suite runs without OPENAI_API_KEY. Better a clear 503 than a
    blank extraction that would be filed as a blank invoice."""
    assert ocr.is_configured() is False
    res = api.post("/api/v1/documents/scan",
                   files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"),
                                   "application/pdf")})
    assert res.status_code == 503
    assert "OPENAI_API_KEY" in res.json()["detail"]


def test_scan_endpoint_requires_a_session(api):
    res = api.post("/api/v1/documents/scan",
                   files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"),
                                   "application/pdf")},
                   headers={"Authorization": ""})
    assert res.status_code == 401


def test_meta_advertises_the_scanner_state(api):
    meta = api.get("/api/meta").json()
    assert meta["scan_enabled"] is False
    assert "application/pdf" in meta["scan_accepts"]
    assert meta["scan_max_bytes"] == ocr.MAX_BYTES
