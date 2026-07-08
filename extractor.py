"""
Extractor Agent — Step 1 of the AI Document Auditor.

Reads a PDF invoice, extracts its raw text, and uses OpenAI's gpt-4o model
to return a strictly-structured JSON object describing the invoice.
"""

import base64
import json
import mimetypes
import os

import pdfplumber
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

from config import OPENAI_API_KEY


# Extensions handled via GPT-4o Vision instead of the pdfplumber text path.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


# System prompt: force the model into a deterministic, JSON-only data extractor.
SYSTEM_PROMPT = (
    "You are a data extraction engine for the technical office of a construction "
    "company. You receive the raw text of a supplier invoice for building "
    "materials. Extract the requested fields and respond with STRICTLY a single "
    "valid JSON object. Do NOT wrap it in markdown code fences. Do NOT add any "
    "conversational text, explanations, or comments. Use exactly these keys:\n"
    '  "provider_name"      -> the name of the supplier/vendor (string)\n'
    '  "date"               -> the invoice date (string, as printed)\n'
    '  "total_amount"       -> the final total payable including VAT\n'
    '  "materials_summary"  -> a short summary of the materials/line items (string)\n'
    '  "project_name"       -> the construction project / building site / job name '
    "this invoice explicitly refers to (e.g. a site address, a building name, or a "
    'reference like "Έργο: ..."). Return null if no specific project is named — do '
    "NOT guess or invent one.\n"
    "\n"
    "LANGUAGE RULES:\n"
    "- The JSON KEYS must always stay in English exactly as listed above "
    "(provider_name, date, total_amount, materials_summary, project_name) for "
    "database compatibility.\n"
    "- The VALUES must be returned in the SAME language as the invoice. If the "
    "invoice is in Greek, keep provider_name in Greek and write the "
    "materials_summary in Greek. Do NOT translate the values to English.\n"
    "\n"
    "NUMERIC RULES:\n"
    "- For total_amount, return the numeric value cleanly. Preserve the original "
    "currency symbol if present, but format the number plainly (e.g. "
    '"1.450,00 €" -> "1450.00 €"). Use a dot as the decimal separator and do not '
    "include thousands separators.\n"
    "\n"
    "If a field cannot be found, use the value null. Output JSON only."
)


def _read_pdf_text(pdf_path):
    """Extract and return all text from a PDF file.

    Raises FileNotFoundError if the file is missing, and ValueError if the PDF
    is corrupt/unreadable or contains no extractable text.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
    except Exception as exc:  # pdfplumber/pdfminer raise a variety of errors
        raise ValueError(f"Could not read PDF (corrupt or unsupported): {exc}") from exc

    full_text = "\n".join(text_parts).strip()
    if not full_text:
        raise ValueError(
            "No extractable text found in the PDF "
            "(it may be a scanned image — OCR would be required)."
        )
    return full_text


def _image_to_data_uri(image_path):
    """Read an image file and return a base64-encoded data URI for the Vision API.

    Raises FileNotFoundError if the file is missing.
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith("image/"):
        # Fall back to the extension if the OS can't guess (e.g. ".jpg" -> jpeg).
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    with open(image_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_user_content(file_path):
    """Build the user message for the model based on the file type.

    PDFs go through pdfplumber as plain text (unchanged). Images are sent as a
    base64 data URI so GPT-4o reads them with its Vision capabilities. The system
    prompt and JSON response format stay identical across both paths.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        data_uri = _image_to_data_uri(file_path)
        return [
            {"type": "text", "text": "Here is the invoice image."},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]

    # Default / .pdf: extract the raw text as before.
    return _read_pdf_text(file_path)


def extract_invoice_data(pdf_path):
    """Read an invoice (PDF or image) and return the extracted invoice fields.

    PDFs are read as text via pdfplumber; JPEG/PNG images are sent to GPT-4o
    Vision as a base64 data URI. Returns a dict with keys: provider_name, date,
    total_amount, materials_summary. Raises on missing/corrupt files; raises
    RuntimeError on API or parsing failures.
    """
    # 1) Build the user content (text for PDFs, image data URI for photos).
    user_content = _build_user_content(pdf_path)

    # 2) Guard against an unconfigured API key.
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        raise RuntimeError(
            "OpenAI API key is not configured. Edit your .env file and set "
            "OPENAI_API_KEY to your real key."
        )

    client = OpenAI(api_key=OPENAI_API_KEY)

    # 3) Ask gpt-4o to extract the structured data.
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except (APIConnectionError, RateLimitError, APIError) as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    content = (response.choices[0].message.content or "").strip()

    # 4) Parse the model output into JSON. Strip stray code fences defensively.
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
        content = content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Model did not return valid JSON. Raw output:\n{content}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Model returned a JSON {type(data).__name__} instead of an object. "
            f"Raw output:\n{content}"
        )

    return data


if __name__ == "__main__":
    from airtable_publisher import publish_to_airtable
    from auditor import check_budget_alerts

    # Switch between the English and Greek sample invoices here.
    pdf_file = "test_greek.pdf"
    print(f"Extracting invoice data from: {pdf_file}\n")
    try:
        result = extract_invoice_data(pdf_file)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (FileNotFoundError, ValueError, RuntimeError) as err:
        print(f"[ERROR] Extraction failed: {err}")
    else:
        # Extraction succeeded — push the structured data straight to Airtable.
        print("\nPublishing to Airtable...")
        try:
            publish_to_airtable(result)
        except RuntimeError as err:
            print(f"[ERROR] Airtable publish failed: {err}")

        # Then run the budget auditor / notifier on the same data.
        print("\nRunning budget audit...")
        check_budget_alerts(result)
