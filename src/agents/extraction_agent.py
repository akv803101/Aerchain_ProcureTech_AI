"""LangGraph ReAct re-extraction agent.

Activates on the re_extract intent. Re-runs the ingestion pipeline on a
specific vendor's file, updates the comparison table, and reports the diff.
"""

import json
import os
from pathlib import Path
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.db.comparison_store import clear_vendor, store_comparison_rows
from src.db.connection import get_db
from src.ingestion.pipeline import _process_one
from src.ingestion.schemas import LineItemNormalized


@tool
async def re_extract_vendor_line(
    rfx_id: str,
    vendor_id: str,
    vendor_dir: str = "data/vendor_responses",
    line_id: Optional[int] = None,
) -> str:
    """Re-run Claude extraction on a vendor's response file.

    Finds the vendor's file, re-runs the full extraction + normalisation
    pipeline, and returns the new rows as JSON.

    Args:
        rfx_id: RFx identifier (e.g. "RFX-001").
        vendor_id: Vendor to re-extract (e.g. "vendor_d").
        vendor_dir: Directory containing vendor response files.
        line_id: If set, return only this line's result; otherwise return all.
    """
    base = Path(vendor_dir)
    matches = (
        list(base.glob(f"{vendor_id}_response.*"))
        + list(base.glob(f"{vendor_id}_quote.*"))
        + list(base.glob(f"{vendor_id}_reply.*"))
    )
    if not matches:
        return json.dumps({"error": f"No file found for {vendor_id} in {vendor_dir}"})

    file_path = str(matches[0])
    rfx_path = f"data/rfx/{rfx_id}.json"

    try:
        with open(rfx_path) as f:
            rfx = json.load(f)
    except FileNotFoundError:
        return json.dumps({"error": f"RFx file not found: {rfx_path}"})

    _, rows, error = await _process_one(file_path, rfx)

    if error or rows is None:
        return json.dumps({"error": error or "Extraction returned 0 line items"})

    if line_id is not None:
        rows = [r for r in rows if r.line_id == line_id]

    return json.dumps([r.model_dump() for r in rows], ensure_ascii=False, default=str)


@tool
async def update_comparison_table(
    rfx_id: str,
    vendor_id: str,
    extracted_rows_json: str,
) -> str:
    """Replace the comparison table entries for a vendor with newly extracted rows.

    Call this after re_extract_vendor_line to persist the new values.

    Args:
        rfx_id: RFx identifier.
        vendor_id: Vendor whose rows to replace.
        extracted_rows_json: JSON string returned by re_extract_vendor_line.
    """
    try:
        rows_data = json.loads(extracted_rows_json)
    except Exception as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    if isinstance(rows_data, dict) and "error" in rows_data:
        return json.dumps({"error": rows_data["error"]})

    try:
        rows = [LineItemNormalized(**r) for r in rows_data]
    except Exception as exc:
        return json.dumps({"error": f"Row parsing failed: {exc}"})

    await clear_vendor(rfx_id, vendor_id)
    await store_comparison_rows(rows)
    return json.dumps({"updated": len(rows), "vendor_id": vendor_id, "rfx_id": rfx_id})


@tool
async def report_diff(
    rfx_id: str,
    vendor_id: str,
    new_rows_json: str,
) -> str:
    """Compare new extraction results against the current DB state for a vendor.

    Returns a list of lines that changed (price, confidence, or flags).

    Args:
        rfx_id: RFx identifier.
        vendor_id: Vendor to compare.
        new_rows_json: JSON string from re_extract_vendor_line.
    """
    try:
        new_list = json.loads(new_rows_json)
    except Exception as exc:
        return json.dumps({"error": f"Invalid JSON: {exc}"})

    if isinstance(new_list, dict) and "error" in new_list:
        return json.dumps({"error": new_list["error"]})

    new_rows = {r["line_id"]: r for r in new_list}

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT line_id, price_inr, confidence, flags FROM comparison WHERE rfx_id = ? AND vendor_id = ?",
            (rfx_id, vendor_id),
        )
        old_rows = {r["line_id"]: dict(r) for r in await cursor.fetchall()}

    diffs = []
    for lid, new in new_rows.items():
        old = old_rows.get(lid)
        if old is None:
            diffs.append({"line_id": lid, "change": "new_line", "new_price": new.get("price_inr")})
        else:
            old_price = old.get("price_inr")
            new_price = new.get("price_inr")
            old_conf = round(old.get("confidence") or 0, 3)
            new_conf = round(new.get("confidence") or 0, 3)
            if old_price != new_price or old_conf != new_conf:
                diffs.append({
                    "line_id": lid,
                    "old_price_inr": old_price,
                    "new_price_inr": new_price,
                    "old_confidence": old_conf,
                    "new_confidence": new_conf,
                    "change": "price_changed" if old_price != new_price else "confidence_changed",
                })

    if not diffs:
        return json.dumps({"no_changes": True, "vendor_id": vendor_id, "lines_checked": len(new_rows)})
    return json.dumps({"vendor_id": vendor_id, "diffs": diffs, "total_changes": len(diffs)}, ensure_ascii=False)


_EXTRACTION_TOOLS = [re_extract_vendor_line, update_comparison_table, report_diff]

_SYSTEM = """You are a procurement re-extraction assistant.

When asked to re-extract or re-check a vendor's data:
1. Call re_extract_vendor_line to run fresh Claude extraction on the vendor's file.
2. Call report_diff to compare new results against what is currently stored.
3. If there are differences, call update_comparison_table to persist the new values.
4. Summarise what changed (prices, confidence scores, lines added/removed).

If 0 lines were extracted, report that the source file may be unreadable and suggest
the buyer provide a clearer version.
"""


def _build_extraction_agent():
    workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
    kwargs: dict = {}
    if workspace_id:
        kwargs["default_headers"] = {"anthropic-workspace-id": workspace_id}

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=4096, **kwargs)
    return create_react_agent(llm, tools=_EXTRACTION_TOOLS, prompt=_SYSTEM)


async def run_extraction_agent(message: str, rfx_id: str = "RFX-001") -> str:
    """Run the re-extraction agent for a vendor mentioned in message.

    Args:
        message: User's re-extraction request (e.g. "re-extract vendor_d").
        rfx_id: RFx context.

    Returns:
        Agent's final answer describing what changed.
    """
    agent = _build_extraction_agent()
    full_msg = f"[RFx: {rfx_id}] {message}"
    result = await agent.ainvoke({"messages": [("user", full_msg)]})
    messages = result.get("messages", [])
    if messages:
        last = messages[-1]
        return last.content if hasattr(last, "content") else str(last)
    return "Re-extraction complete."
