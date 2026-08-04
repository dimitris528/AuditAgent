"""
Invoice OCR — the arithmetic done AFTER the model answers, plus the endpoint's
unconfigured path.

Nothing here calls the Claude API. What is worth testing is exactly the part
that is not the model: the derivations, the tolerance, and the guards against a
misread landing in the books.
"""

import io

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


# --- Endpoint -------------------------------------------------------------
def test_scan_endpoint_reports_when_no_key_is_configured(api):
    """The suite runs without ANTHROPIC_API_KEY. Better a clear 503 than a
    blank extraction that would be filed as a blank invoice."""
    assert ocr.is_configured() is False
    res = api.post("/api/v1/documents/scan",
                   files={"file": ("invoice.pdf", io.BytesIO(b"%PDF-1.4"),
                                   "application/pdf")})
    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


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
