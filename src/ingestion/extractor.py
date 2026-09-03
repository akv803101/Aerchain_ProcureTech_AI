"""Vendor document extraction via Claude (vision + text).

Extraction prompt is taken verbatim from PRD Section 6. Do not modify it.
"""

import base64
import json
import logging
import re
import sys
from pathlib import Path

import anthropic

log = logging.getLogger(__name__)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

from src.ingestion.schemas import ExtractionOutput, LineItemRaw

# ── Locked extraction prompt (PRD Section 6) ────────────────────────────────
EXTRACTION_PROMPT = """You are a procurement data extraction specialist.

Extract all line items from the vendor response below.
The RFx requested quotes in INR per box.

For each line item return:
{
  "line_id": int or null if not identifiable,
  "description": str,
  "price": float or null,
  "unit": str or null,
  "currency": "INR" or detected currency,
  "flags": [],
  "flag_notes": ""
}

Use ONLY these flags when applicable:
  PRICE_MISSING         - no price found for this line
  PRICE_AMBIGUOUS       - multiple prices present, unclear which applies
  UNIT_INFERRED         - unit not stated, inferred from context
  UNIT_MISMATCH         - unit differs from RFx specification (per box)
  CURRENCY_CONVERTED    - price was in foreign currency
  TEMPORAL_REFERENCE    - vendor referenced prior quote ("same as last year")
  LINE_NOT_IN_RFX       - vendor quoted item not in RFx
  VALUE_IN_PROSE        - price found inside paragraph text
  LOW_LEGIBILITY        - text unclear due to image quality

Also extract:
{
  "questionnaire": {
    "iso_certified": "Yes"/"No"/null,
    "rejection_rate": float or null,
    "lead_time_days": int or null,
    "manufacturing_location": str or null,
    "deviations": str or null
  },
  "freight": float or null,
  "freight_notes": str or null,
  "freight_unquantified": true if vendor mentions freight but gives no number,
  "discount_condition": str or null,
  "discount_pct": float or null,
  "quote_validity_days": int or null
}

Rules:
- Do not invent flags. Set a flag only when the condition is clearly present.
- Do not guess prices. If price is unclear, set PRICE_MISSING or PRICE_AMBIGUOUS.
- Do not hallucinate line items. Extract only what is present.
- TEMPORAL_REFERENCE must be set whenever vendor references prior rates.
- freight_unquantified must be true whenever vendor mentions freight without a number.
- discount_condition and discount_pct must be extracted from footnotes or prose if present.
- Return valid JSON only. No prose before or after.

Return a single JSON object:
{
  "line_items": [...],
  "questionnaire": {...},
  "freight": ...,
  "freight_notes": ...,
  "freight_unquantified": ...,
  "discount_condition": ...,
  "discount_pct": ...,
  "quote_validity_days": ...
}"""


_EXTRACTION_RULES = """
Extraction rules:
- Return prices EXACTLY as written in the document. Do NOT pre-compute or normalize.
  Example: if document says "₹9,800 per 100 units", return price=9800 and unit="per 100 units".
- UNIT_MISMATCH applies only when the UNIT TYPE differs from per-box (e.g. per kg, per dozen, per 100 units).
  Do NOT set UNIT_MISMATCH when the price is in a foreign currency but the unit is still per box.
- CURRENCY_CONVERTED applies when prices are in a foreign currency (e.g. USD, EUR).
""".strip()


def _rfx_context(rfx: dict) -> str:
    lines = []
    for item in rfx.get("line_items", []):
        lines.append(
            f"  Line {item['id']:02d}: {item['description']} | "
            f"Spec: {item.get('spec', '')} | Qty: {item.get('qty', '')} per box"
        )
    return "RFx line items for context:\n" + "\n".join(lines)


def _parse_claude_json(text: str) -> dict:
    """Extract JSON from Claude response, stripping markdown fences if present."""
    # Remove ```json ... ``` wrapping if present
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def _build_extraction_output(raw: dict) -> ExtractionOutput:
    items = []
    for li in raw.get("line_items", []):
        items.append(
            LineItemRaw(
                line_id=li.get("line_id"),
                description=li.get("description", ""),
                price=li.get("price"),
                unit=li.get("unit"),
                currency=li.get("currency"),
                flags=li.get("flags", []),
                flag_notes=li.get("flag_notes", ""),
            )
        )
    q = raw.get("questionnaire") or {}
    return ExtractionOutput(
        line_items=items,
        questionnaire=q,
        freight=raw.get("freight"),
        freight_notes=raw.get("freight_notes"),
        freight_unquantified=bool(raw.get("freight_unquantified", False)),
        discount_condition=raw.get("discount_condition"),
        discount_pct=raw.get("discount_pct"),
        quote_validity_days=raw.get("quote_validity_days"),
    )


def _empty_output(rfx: dict) -> ExtractionOutput:
    """Fallback when extraction completely fails — one EXTRACTION_FAILED row per RFx line."""
    items = [
        LineItemRaw(
            line_id=item["id"],
            description=item["description"],
            price=None,
            unit=None,
            currency=None,
            flags=["EXTRACTION_FAILED", "PRICE_MISSING"],
            flag_notes="Extraction failed for this document.",
        )
        for item in rfx.get("line_items", [])
    ]
    return ExtractionOutput(line_items=items)


# ── Per-format content readers ───────────────────────────────────────────────

def _read_excel(file_path: str) -> str:
    import pandas as pd
    frames = pd.read_excel(file_path, sheet_name=None, header=None)
    parts = []
    for sheet_name, df in frames.items():
        parts.append(f"=== Sheet: {sheet_name} ===")
        parts.append(df.fillna("").to_string(index=False, header=False))
    return "\n\n".join(parts)


def _read_pdf(file_path: str) -> tuple[str, dict[int, int]]:
    """Returns (full_text, {page_number: char_offset}) for page_ref tracking."""
    import pdfplumber
    parts = []
    offsets: dict[int, int] = {}
    pos = 0
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            offsets[i] = pos
            text = page.extract_text() or ""
            parts.append(f"--- Page {i} ---\n{text}")
            pos += len(parts[-1]) + 1
    return "\n\n".join(parts), offsets


def _read_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    return "\n".join(p.text for p in doc.paragraphs)


def _read_text(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8", errors="replace")


def _read_eml(file_path: str) -> tuple[str, list[tuple[str, bytes]]]:
    """Parse an .eml file.

    Returns (body_text, [(filename, raw_bytes), ...]) where the list contains
    image/PDF attachments that should be extracted separately.
    """
    import email as email_mod
    import re
    from email import policy as email_policy

    with open(file_path, "rb") as f:
        msg = email_mod.message_from_bytes(f.read(), policy=email_policy.compat32)

    body_parts: list[str] = []
    attachments: list[tuple[str, bytes]] = []

    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    if subject:
        body_parts.append(f"Subject: {subject}")
    if sender:
        body_parts.append(f"From: {sender}")

    for part in msg.walk():
        ct = part.get_content_type()
        disp = str(part.get("Content-Disposition") or "")
        fname = part.get_filename() or ""

        if "attachment" in disp or fname:
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append((fname, payload))
        elif ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                body_parts.append(payload.decode("utf-8", errors="replace"))
        elif ct == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                html = payload.decode("utf-8", errors="replace")
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                body_parts.append(text)

    return "\n\n".join(body_parts), attachments


# ── Shared client factory ────────────────────────────────────────────────────

def _make_client() -> anthropic.AsyncAnthropic:
    import os
    workspace_id = (
        os.getenv("ANTHROPIC_WORKSPACE_ID")
        or os.getenv("ANT_WS_ID")
    )
    _err(f"[client] workspace_id resolved={workspace_id!r}")
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else {}
    return anthropic.AsyncAnthropic(default_headers=headers or None)


# ── Claude API call (text) ───────────────────────────────────────────────────

async def _call_claude_text(content: str, rfx: dict) -> ExtractionOutput:
    client = _make_client()
    user_msg = f"{_rfx_context(rfx)}\n\n{_EXTRACTION_RULES}\n\nVendor document content:\n{content}"
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw_text = response.content[0].text
    try:
        raw = _parse_claude_json(raw_text)
        return _build_extraction_output(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.error("JSON parse failed in text extraction: %s | raw=%s", exc, raw_text[:200])
        return _empty_output(rfx)


# ── Claude API call (vision) ─────────────────────────────────────────────────

async def _call_claude_vision(file_path: str, rfx: dict) -> ExtractionOutput:
    client = _make_client()
    ext = Path(file_path).suffix.lstrip(".").lower()
    media_type_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "tiff": "image/tiff"}
    media_type = media_type_map.get(ext, "image/jpeg")

    with open(file_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()

    user_msg = (
        f"{_rfx_context(rfx)}\n\n{_EXTRACTION_RULES}\n\n"
        "Extract all vendor pricing information from the image above.\n"
        "IMPORTANT: This image has known quality degradation (blur + rotation). "
        "Apply LOW_LEGIBILITY flag to ALL line items since image quality affects readability throughout. "
        "Do NOT set UNIT_MISMATCH unless the unit type itself (not the currency) differs from per box."
    )
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=EXTRACTION_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": user_msg},
                ],
            }
        ],
    )
    raw_text = response.content[0].text
    try:
        raw = _parse_claude_json(raw_text)
        return _build_extraction_output(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.error("JSON parse failed in vision extraction: %s | raw=%s", exc, raw_text[:200])
        return _empty_output(rfx)


# ── Public API ───────────────────────────────────────────────────────────────

async def extract_excel(file_path: str, rfx: dict) -> ExtractionOutput:
    _err(f"[extractor] extract_excel starting: {file_path}")
    try:
        content = _read_excel(file_path)
        _err(f"[extractor] excel read ok, calling claude")
        result = await _call_claude_text(content, rfx)
        _err(f"[extractor] excel done: {len(result.line_items)} items")
        return result
    except Exception as exc:
        _err(f"[extractor] extract_excel FAILED: {type(exc).__name__}: {exc}")
        return _empty_output(rfx)


async def _call_claude_pdf_direct(file_path: str, rfx: dict) -> ExtractionOutput:
    """Send the PDF binary directly to Claude as a document block.

    Claude handles both text-based and image-heavy (scanned) PDFs this way,
    without needing a separate OCR step.
    """
    import base64
    client = _make_client()
    with open(file_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    user_content = [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
        },
        {
            "type": "text",
            "text": (
                f"{_rfx_context(rfx)}\n\n{_EXTRACTION_RULES}\n\n"
                "Extract all vendor pricing information from the PDF above. "
                "If any page appears to be a scanned image apply LOW_LEGIBILITY to those line items."
            ),
        },
    ]
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=EXTRACTION_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw_text = response.content[0].text
    try:
        raw = _parse_claude_json(raw_text)
        return _build_extraction_output(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.error("JSON parse failed in PDF direct extraction: %s | raw=%s", exc, raw_text[:200])
        return _empty_output(rfx)


async def extract_pdf(file_path: str, rfx: dict) -> ExtractionOutput:
    """Extract from PDF.

    Strategy:
     1. Extract text with pdfplumber.
     2. If text is sparse (< 150 chars total) the PDF is likely scanned/image-heavy
        → fall through to the direct-document path which Claude handles natively.
     3. Direct-document path also serves as fallback on any text-extraction error.
    """
    try:
        content, _ = _read_pdf(file_path)
        if len(content.strip()) >= 150:
            return await _call_claude_text(content, rfx)
    except Exception as exc:
        log.warning("PDF text read failed for %s, falling back to direct: %s", file_path, exc)
    # Sparse text or error → send PDF directly (handles embedded images too)
    try:
        return await _call_claude_pdf_direct(file_path, rfx)
    except Exception as exc:
        log.error("extract_pdf failed for %s: %s", file_path, exc, exc_info=True)
        return _empty_output(rfx)


async def extract_docx(file_path: str, rfx: dict) -> ExtractionOutput:
    try:
        content = _read_docx(file_path)
        return await _call_claude_text(content, rfx)
    except Exception as exc:
        log.error("extract_docx failed for %s: %s", file_path, exc, exc_info=True)
        return _empty_output(rfx)


async def extract_image(file_path: str, rfx: dict) -> ExtractionOutput:
    try:
        return await _call_claude_vision(file_path, rfx)
    except Exception as exc:
        log.error("extract_image failed for %s: %s", file_path, exc, exc_info=True)
        return _empty_output(rfx)


async def extract_text(file_path: str, rfx: dict) -> ExtractionOutput:
    try:
        content = _read_text(file_path)
        return await _call_claude_text(content, rfx)
    except Exception as exc:
        log.error("extract_text failed for %s: %s", file_path, exc, exc_info=True)
        return _empty_output(rfx)


async def extract_email(file_path: str, rfx: dict) -> ExtractionOutput:
    """Extract from .eml email files.

    Processes the email body as text, then merges any image/PDF attachments
    by running them through their respective extractors and combining results.
    """
    import os, tempfile

    try:
        body_text, attachments = _read_eml(file_path)
    except Exception:
        return _empty_output(rfx)

    # Extract from the email body text
    results: list[ExtractionOutput] = []
    if body_text.strip():
        try:
            results.append(await _call_claude_text(body_text, rfx))
        except Exception:
            pass

    # Extract from each attachment
    for fname, raw_bytes in attachments:
        ext = os.path.splitext(fname)[1].lstrip(".").lower()
        if ext not in {"pdf", "jpg", "jpeg", "png", "tiff", "gif", "xlsx", "xls", "docx"}:
            continue
        try:
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name
            if ext == "pdf":
                result = await extract_pdf(tmp_path, rfx)
            elif ext in {"jpg", "jpeg", "png", "tiff", "gif"}:
                result = await extract_image(tmp_path, rfx)
            elif ext in {"xlsx", "xls"}:
                result = await extract_excel(tmp_path, rfx)
            elif ext in {"docx", "doc"}:
                result = await extract_docx(tmp_path, rfx)
            else:
                continue
            results.append(result)
        except Exception:
            continue
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    if not results:
        return _empty_output(rfx)

    # Merge: prefer the result with the most non-missing line items
    return max(results, key=lambda r: sum(1 for li in r.line_items if li.price is not None))
