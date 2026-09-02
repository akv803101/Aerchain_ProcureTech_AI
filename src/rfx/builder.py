"""RFx Builder — creates a structured RFx JSON from a plain-text spec.

Produces the same format as data/rfx/RFX-001.json so the ingestion pipeline
can use it directly.
"""

import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


def _next_rfx_id(output_dir: str = "data/rfx") -> str:
    """Auto-increment RFX ID based on existing files in output_dir."""
    existing = list(Path(output_dir).glob("RFX-*.json"))
    if not existing:
        return "RFX-001"
    nums = []
    for p in existing:
        m = re.search(r"RFX-(\d+)", p.stem)
        if m:
            nums.append(int(m.group(1)))
    return f"RFX-{max(nums) + 1:03d}"


def build_rfx(
    category: str,
    line_items: list[dict],
    rfx_id: Optional[str] = None,
    issued_date: Optional[str] = None,
    deadline_days: int = 7,
    output_dir: str = "data/rfx",
    save: bool = True,
) -> dict:
    """Build and optionally save an RFx JSON.

    Args:
        category: Procurement category (e.g. "Corrugated Packaging").
        line_items: List of dicts with keys: description, spec, qty, unit (all str/int).
                    Missing keys get sensible defaults. 'id' is auto-assigned if absent.
        rfx_id: RFx identifier. Auto-generated from existing files if None.
        issued_date: ISO date string (YYYY-MM-DD). Defaults to today.
        deadline_days: Days from issued_date to deadline. Default 7.
        output_dir: Directory to write the JSON file.
        save: Write the file to disk. Set False for in-memory use.

    Returns:
        The RFx dict.
    """
    today = date.today().isoformat()
    rfx_id = rfx_id or _next_rfx_id(output_dir)
    issued = issued_date or today
    try:
        issued_dt = date.fromisoformat(issued)
        deadline = (issued_dt + timedelta(days=deadline_days)).isoformat()
    except ValueError:
        deadline = (date.today() + timedelta(days=deadline_days)).isoformat()

    normalized_items = []
    for idx, item in enumerate(line_items, start=1):
        normalized_items.append({
            "id": item.get("id", idx),
            "description": item.get("description", f"Item {idx}"),
            "spec": item.get("spec", ""),
            "qty": item.get("qty", 0),
            "unit": item.get("unit", "per box"),
        })

    rfx = {
        "rfx_id": rfx_id,
        "category": category,
        "issued_date": issued,
        "deadline": deadline,
        "line_items": normalized_items,
    }

    if save:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        out_path = Path(output_dir) / f"{rfx_id}.json"
        out_path.write_text(json.dumps(rfx, indent=2, ensure_ascii=False))

    return rfx


def rfx_from_text(text: str, save: bool = False) -> dict:
    """Parse a minimal free-text spec into an RFx dict.

    Accepts newline-separated lines like:
        category: Office Supplies
        item: A4 paper, spec: 80gsm, qty: 10000
        item: Ballpoint pens, spec: blue ink, qty: 5000

    Returns an RFx dict (does NOT save by default).
    """
    category = "General"
    items = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        cat_m = re.match(r"category\s*:\s*(.+)", line, re.I)
        if cat_m:
            category = cat_m.group(1).strip()
            continue

        item_m = re.match(r"item\s*:\s*(.+)", line, re.I)
        if item_m:
            raw = item_m.group(1)
            # Split on comma-separated key:value pairs; first token may be description only
            segments = [s.strip() for s in raw.split(",")]
            kvs: dict[str, str] = {}
            description = ""
            for seg in segments:
                if ":" in seg:
                    k, _, v = seg.partition(":")
                    kvs[k.strip().lower()] = v.strip()
                else:
                    description = seg  # plain token before any key:value = description
            if not description:
                description = raw  # fallback
            items.append({
                "description": description,
                "spec": kvs.get("spec", ""),
                "qty": int(re.sub(r"\D", "", kvs.get("qty", "0")) or "0"),
                "unit": kvs.get("unit", "per box"),
            })

    return build_rfx(category=category, line_items=items, save=save)
