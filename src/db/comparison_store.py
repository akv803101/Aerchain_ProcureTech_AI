import json
from datetime import datetime, timezone

from src.db.connection import get_db
from src.ingestion.schemas import LineItemNormalized


async def store_comparison_rows(rows: list[LineItemNormalized]) -> None:
    async with get_db() as db:
        for row in rows:
            await db.execute(
                """
                INSERT INTO comparison (
                    rfx_id, vendor_id, line_id, description,
                    price_raw, price_inr, unit_raw, unit_normalized, currency_raw,
                    confidence, flags, flag_notes,
                    source_file, page_ref, extraction_status, extracted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.rfx_id,
                    row.vendor_id,
                    row.line_id or 0,
                    row.description,
                    row.price_raw,
                    row.price_inr,
                    row.unit_raw,
                    row.unit_normalized,
                    row.currency_raw,
                    row.confidence,
                    json.dumps(row.flags),
                    json.dumps(row.flag_notes),
                    row.source_file,
                    row.page_ref,
                    row.extraction_status,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()


async def clear_vendor(rfx_id: str, vendor_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM comparison WHERE rfx_id = ? AND vendor_id = ?",
            (rfx_id, vendor_id),
        )
        await db.commit()
