"""Six LangChain tools for the Query Agent to interrogate the comparison DB."""

import json
from typing import Optional

from langchain_core.tools import tool

from src.db.connection import get_db


@tool
async def get_price_comparison(rfx_id: str, line_id: Optional[int] = None) -> str:
    """Return normalized INR prices for every vendor for a given RFx line (or all lines).

    Use this to see what each vendor quoted for a specific item or the full RFx.
    Returns a JSON list sorted by line_id then price_inr ascending.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        line_id: Specific line number to retrieve. Omit to get all lines.
    """
    async with get_db() as db:
        if line_id is not None:
            cursor = await db.execute(
                """
                SELECT vendor_id, line_id, description, price_inr, confidence, flags
                FROM comparison
                WHERE rfx_id = ? AND line_id = ?
                ORDER BY price_inr ASC NULLS LAST
                """,
                (rfx_id, line_id),
            )
        else:
            cursor = await db.execute(
                """
                SELECT vendor_id, line_id, description, price_inr, confidence, flags
                FROM comparison
                WHERE rfx_id = ?
                ORDER BY line_id ASC, price_inr ASC NULLS LAST
                """,
                (rfx_id,),
            )
        rows = await cursor.fetchall()

    result = [
        {
            "vendor_id": r["vendor_id"],
            "line_id": r["line_id"],
            "description": r["description"],
            "price_inr": r["price_inr"],
            "confidence": round(r["confidence"], 2) if r["confidence"] is not None else None,
            "flags": json.loads(r["flags"]) if r["flags"] else [],
        }
        for r in rows
    ]
    return json.dumps(result, ensure_ascii=False)


@tool
async def get_lowest_price(
    rfx_id: str, line_id: int, min_confidence: float = 0.5
) -> str:
    """Return the vendor with the lowest price for a specific RFx line item.

    Only considers vendors whose confidence score meets the minimum threshold,
    ensuring low-quality extractions (blurry images, missing prices) are excluded.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        line_id: Line item number to check.
        min_confidence: Minimum confidence score (0–1). Default 0.5.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT vendor_id, line_id, description, price_inr, confidence, flags
            FROM comparison
            WHERE rfx_id = ? AND line_id = ? AND confidence >= ? AND price_inr IS NOT NULL
            ORDER BY price_inr ASC
            LIMIT 1
            """,
            (rfx_id, line_id, min_confidence),
        )
        row = await cursor.fetchone()

    if row is None:
        return json.dumps({"error": f"No qualifying vendor found for line {line_id} with confidence >= {min_confidence}"})

    return json.dumps({
        "vendor_id": row["vendor_id"],
        "line_id": row["line_id"],
        "description": row["description"],
        "price_inr": row["price_inr"],
        "confidence": round(row["confidence"], 2),
        "flags": json.loads(row["flags"]) if row["flags"] else [],
    }, ensure_ascii=False)


@tool
async def get_vendor_terms(rfx_id: str, vendor_id: Optional[str] = None) -> str:
    """Return freight and discount terms extracted from vendor responses.

    Covers freight cost (if quoted), whether freight is unquantified,
    volume discount conditions, and discount percentage.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        vendor_id: Specific vendor to filter. Omit to return all vendors.
    """
    if vendor_id is not None:
        vendor_id = vendor_id.lower()
    async with get_db() as db:
        if vendor_id is not None:
            cursor = await db.execute(
                """
                SELECT vendor_id, freight_inr, freight_notes, freight_unquantified,
                       discount_condition, discount_pct
                FROM vendor_terms
                WHERE rfx_id = ? AND vendor_id = ?
                """,
                (rfx_id, vendor_id),
            )
        else:
            cursor = await db.execute(
                """
                SELECT vendor_id, freight_inr, freight_notes, freight_unquantified,
                       discount_condition, discount_pct
                FROM vendor_terms
                WHERE rfx_id = ?
                ORDER BY vendor_id
                """,
                (rfx_id,),
            )
        rows = await cursor.fetchall()

    result = [
        {
            "vendor_id": r["vendor_id"],
            "freight_inr": r["freight_inr"],
            "freight_notes": r["freight_notes"],
            "freight_unquantified": bool(r["freight_unquantified"]),
            "discount_condition": r["discount_condition"],
            "discount_pct": r["discount_pct"],
        }
        for r in rows
    ]
    return json.dumps(result, ensure_ascii=False)


@tool
async def get_questionnaire_responses(rfx_id: str, vendor_id: Optional[str] = None) -> str:
    """Return qualification questionnaire answers for vendors.

    Covers ISO certification, historical rejection rate, lead time,
    manufacturing location, deviations noted, and quote validity.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        vendor_id: Specific vendor to filter. Omit to return all vendors.
    """
    if vendor_id is not None:
        vendor_id = vendor_id.lower()
    async with get_db() as db:
        if vendor_id is not None:
            cursor = await db.execute(
                """
                SELECT vendor_id, iso_certified, rejection_rate, lead_time_days,
                       manufacturing_location, deviations, quote_validity_days
                FROM questionnaire
                WHERE rfx_id = ? AND vendor_id = ?
                """,
                (rfx_id, vendor_id),
            )
        else:
            cursor = await db.execute(
                """
                SELECT vendor_id, iso_certified, rejection_rate, lead_time_days,
                       manufacturing_location, deviations, quote_validity_days
                FROM questionnaire
                WHERE rfx_id = ?
                ORDER BY vendor_id
                """,
                (rfx_id,),
            )
        rows = await cursor.fetchall()

    result = [
        {
            "vendor_id": r["vendor_id"],
            "iso_certified": r["iso_certified"],
            "rejection_rate": r["rejection_rate"],
            "lead_time_days": r["lead_time_days"],
            "manufacturing_location": r["manufacturing_location"],
            "deviations": r["deviations"],
            "quote_validity_days": r["quote_validity_days"],
        }
        for r in rows
    ]
    return json.dumps(result, ensure_ascii=False)


@tool
async def get_flag_summary(rfx_id: str, min_confidence: Optional[float] = None) -> str:
    """Return a per-vendor flag summary for a given RFx.

    Reports total lines, flagged line count, average confidence score,
    and individual flag frequencies. Useful for assessing data quality
    and spotting vendors with extraction issues.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        min_confidence: If set, only include lines at or above this confidence threshold.
    """
    async with get_db() as db:
        if min_confidence is not None:
            cursor = await db.execute(
                """
                SELECT vendor_id, confidence, flags
                FROM comparison
                WHERE rfx_id = ? AND confidence >= ?
                ORDER BY vendor_id
                """,
                (rfx_id, min_confidence),
            )
        else:
            cursor = await db.execute(
                """
                SELECT vendor_id, confidence, flags
                FROM comparison
                WHERE rfx_id = ?
                ORDER BY vendor_id
                """,
                (rfx_id,),
            )
        rows = await cursor.fetchall()

    summary: dict[str, dict] = {}
    for r in rows:
        vid = r["vendor_id"]
        if vid not in summary:
            summary[vid] = {"total_lines": 0, "flagged_lines": 0, "confidence_sum": 0.0, "flag_counts": {}}
        s = summary[vid]
        s["total_lines"] += 1
        conf = r["confidence"] or 0.0
        s["confidence_sum"] += conf
        flags = json.loads(r["flags"]) if r["flags"] else []
        if flags:
            s["flagged_lines"] += 1
        for f in flags:
            s["flag_counts"][f] = s["flag_counts"].get(f, 0) + 1

    result = {
        vid: {
            "total_lines": s["total_lines"],
            "flagged_lines": s["flagged_lines"],
            "avg_confidence": round(s["confidence_sum"] / s["total_lines"], 3) if s["total_lines"] else 0.0,
            "flag_counts": s["flag_counts"],
        }
        for vid, s in summary.items()
    }
    return json.dumps(result, ensure_ascii=False)


@tool
async def compute_price_delta(
    rfx_id: str, vendor_a: str, vendor_b: str, line_id: Optional[int] = None
) -> str:
    """Compute the percentage price difference between two vendors line by line.

    Positive delta means vendor_b is more expensive than vendor_a.
    Skips lines where either vendor has no price (None).

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        vendor_a: First vendor ID (baseline).
        vendor_b: Second vendor ID (comparison).
        line_id: Specific line to compare. Omit for all lines.
    """
    vendor_a = vendor_a.lower()
    vendor_b = vendor_b.lower()
    async with get_db() as db:
        filter_clause = "AND a.line_id = ?" if line_id is not None else ""
        params = [rfx_id, vendor_a, rfx_id, vendor_b]
        if line_id is not None:
            params.append(line_id)

        cursor = await db.execute(
            f"""
            SELECT a.line_id, a.description,
                   a.price_inr AS price_a, b.price_inr AS price_b,
                   a.confidence AS conf_a, b.confidence AS conf_b
            FROM comparison a
            JOIN comparison b ON a.rfx_id = b.rfx_id AND a.line_id = b.line_id
            WHERE a.rfx_id = ? AND a.vendor_id = ?
              AND b.rfx_id = ? AND b.vendor_id = ?
              {filter_clause}
            ORDER BY a.line_id
            """,
            params,
        )
        rows = await cursor.fetchall()

    result = []
    for r in rows:
        pa, pb = r["price_a"], r["price_b"]
        if pa is None or pb is None:
            continue
        delta_pct = round((pb - pa) / pa * 100, 2) if pa != 0 else None
        result.append({
            "line_id": r["line_id"],
            "description": r["description"],
            f"price_{vendor_a}": pa,
            f"price_{vendor_b}": pb,
            "delta_pct": delta_pct,
            "cheaper": vendor_a if (delta_pct is not None and delta_pct > 0) else vendor_b,
        })

    return json.dumps(result, ensure_ascii=False)


# Exported list for the agent to bind
ALL_TOOLS = [
    get_price_comparison,
    get_lowest_price,
    get_vendor_terms,
    get_questionnaire_responses,
    get_flag_summary,
    compute_price_delta,
]
