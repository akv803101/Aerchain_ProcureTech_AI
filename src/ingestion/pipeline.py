"""Ingestion pipeline: detect → extract → normalize → store."""

import asyncio
import json
import logging
import os
from pathlib import Path

from src.db.comparison_store import clear_vendor, store_comparison_rows
from src.db.questionnaire_store import store_questionnaire, store_vendor_terms
from src.ingestion.confidence import compute_confidence, flags_to_notes
from src.ingestion.detector import detect_format
from src.ingestion.extractor import (
    extract_docx,
    extract_email,
    extract_excel,
    extract_image,
    extract_pdf,
    extract_text,
)
from src.ingestion.normaliser import normalise_currency, normalise_unit
from src.ingestion.schemas import IngestionSummary, LineItemNormalized

log = logging.getLogger(__name__)


def _vendor_id_from_filename(filename: str) -> str:
    """'vendor_a_response.xlsx' → 'vendor_a'"""
    stem = Path(filename).stem
    for suffix in ("_response", "_quote", "_reply"):
        if suffix in stem:
            return stem[: stem.index(suffix)]
    return stem


async def _process_one(
    file_path: str,
    rfx: dict,
) -> tuple[str, list[LineItemNormalized] | None, str | None]:
    """Extract and normalise one vendor file.

    Returns (vendor_id, rows_or_None, error_or_None).
    """
    vendor_id = _vendor_id_from_filename(os.path.basename(file_path))
    rfx_id = rfx.get("rfx_id", "RFX-001")
    source_file = os.path.basename(file_path)
    fmt = detect_format(file_path)

    extractors = {
        "excel": extract_excel,
        "pdf": extract_pdf,
        "docx": extract_docx,
        "image": extract_image,
        "text": extract_text,
        "email": extract_email,
    }
    extract_fn = extractors[fmt]

    try:
        result = await extract_fn(file_path, rfx)
    except Exception as exc:
        return vendor_id, None, str(exc)

    if not result.line_items:
        return vendor_id, None, "Extraction returned 0 line items"

    # Write terms & questionnaire first (non-fatal if missing)
    try:
        await store_vendor_terms(
            rfx_id=rfx_id,
            vendor_id=vendor_id,
            source_file=source_file,
            freight_inr=result.freight,
            freight_notes=result.freight_notes,
            freight_unquantified=result.freight_unquantified,
            discount_condition=result.discount_condition,
            discount_pct=result.discount_pct,
        )
        q = result.questionnaire or {}
        await store_questionnaire(
            rfx_id=rfx_id,
            vendor_id=vendor_id,
            source_file=source_file,
            iso_certified=q.get("iso_certified"),
            rejection_rate=q.get("rejection_rate"),
            lead_time_days=q.get("lead_time_days"),
            manufacturing_location=q.get("manufacturing_location"),
            deviations=q.get("deviations"),
            quote_validity_days=result.quote_validity_days,
        )
    except Exception as exc:
        log.warning("terms/questionnaire store failed for %s: %s", vendor_id, exc)

    # Normalize and build comparison rows
    rows: list[LineItemNormalized] = []
    for item in result.line_items:
        flags: list[str] = list(item.flags)

        # Currency normalization
        price_inr, currency_flags = normalise_currency(item.price, item.currency)
        for f in currency_flags:
            if f not in flags:
                flags.append(f)

        # Unit normalization
        description = item.description or ""
        price_normalized, unit_normalized, unit_flags = normalise_unit(
            price_inr, item.unit, description
        )
        for f in unit_flags:
            if f not in flags:
                flags.append(f)

        confidence = compute_confidence(flags)

        extraction_status = "ok"
        if price_normalized is None and "PRICE_MISSING" not in flags and "TEMPORAL_REFERENCE" not in flags:
            extraction_status = "missing_price"
        if "EXTRACTION_FAILED" in flags:
            extraction_status = "failed"

        rows.append(
            LineItemNormalized(
                vendor_id=vendor_id,
                rfx_id=rfx_id,
                line_id=item.line_id,
                description=description,
                price_raw=item.price,
                price_inr=price_normalized,
                unit_raw=item.unit,
                unit_normalized=unit_normalized,
                currency_raw=item.currency,
                confidence=confidence,
                flags=flags,
                flag_notes=flags_to_notes(flags),
                source_file=source_file,
                page_ref=None,
                extraction_status=extraction_status,
            )
        )

    return vendor_id, rows, None


async def run_ingestion(
    rfx_path: str,
    vendor_dir: str,
) -> IngestionSummary:
    """Main entry point: process all vendor files for one RFx.

    Scans vendor_dir for files matching vendor_*_response.*,
    extracts each, normalizes, and stores to DB.
    """
    with open(rfx_path) as f:
        rfx = json.load(f)

    rfx_id = rfx.get("rfx_id", "RFX-001")
    vendor_files = sorted(
        p for p in Path(vendor_dir).iterdir()
        if p.is_file() and "vendor_" in p.name and "response" in p.name
    )

    if not vendor_files:
        return IngestionSummary(
            vendors_processed=0,
            vendors_failed=[],
            failure_detail={},
            total_lines=0,
            total_flags={},
            ready=False,
        )

    vendors_failed: list[str] = []
    failure_detail: dict[str, str] = {}
    total_lines = 0
    flag_counts: dict[str, int] = {}

    # Process all vendors concurrently — all LLM calls run in parallel
    results = await asyncio.gather(
        *[_process_one(str(vf), rfx) for vf in vendor_files],
        return_exceptions=False,
    )

    for vendor_id, rows, error in results:
        if error or rows is None:
            vendors_failed.append(vendor_id)
            failure_detail[vendor_id] = error or "unknown"
            log.error("Ingestion failed for %s: %s", vendor_id, error)
            continue

        await clear_vendor(rfx_id, vendor_id)
        await store_comparison_rows(rows)
        total_lines += len(rows)
        for row in rows:
            for flag in row.flags:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
        log.info("Ingested %s: %d lines", vendor_id, len(rows))

    processed = len(vendor_files) - len(vendors_failed)
    return IngestionSummary(
        vendors_processed=processed,
        vendors_failed=vendors_failed,
        failure_detail=failure_detail,
        total_lines=total_lines,
        total_flags=flag_counts,
        ready=processed > 0,
    )
