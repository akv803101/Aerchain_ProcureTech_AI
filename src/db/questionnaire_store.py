from datetime import datetime, timezone

from src.db.connection import get_db


async def store_vendor_terms(
    rfx_id: str,
    vendor_id: str,
    source_file: str,
    freight_inr: float | None,
    freight_notes: str | None,
    freight_unquantified: bool,
    discount_condition: str | None,
    discount_pct: float | None,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO vendor_terms (
                rfx_id, vendor_id,
                freight_inr, freight_notes, freight_unquantified,
                discount_condition, discount_pct,
                source_file, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rfx_id,
                vendor_id,
                freight_inr,
                freight_notes,
                1 if freight_unquantified else 0,
                discount_condition,
                discount_pct,
                source_file,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def store_questionnaire(
    rfx_id: str,
    vendor_id: str,
    source_file: str,
    iso_certified: str | None,
    rejection_rate: float | None,
    lead_time_days: int | None,
    manufacturing_location: str | None,
    deviations: str | None,
    quote_validity_days: int | None,
) -> None:
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO questionnaire (
                rfx_id, vendor_id,
                iso_certified, rejection_rate, lead_time_days,
                manufacturing_location, deviations, quote_validity_days,
                source_file, extracted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rfx_id,
                vendor_id,
                iso_certified,
                rejection_rate,
                lead_time_days,
                manufacturing_location,
                deviations,
                quote_validity_days,
                source_file,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
