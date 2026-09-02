"""POST /rfx/create — AI co-pilot generates an RFx document from a description."""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import anthropic
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_CLIENT = None


def _make_client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else {}
        _CLIENT = anthropic.Anthropic(default_headers=headers)
    return _CLIENT


_SYSTEM = """You are a procurement AI co-pilot. When given a buyer's description of what they need to procure, you generate a structured RFx (Request for Quotation) document as JSON.

Output ONLY valid JSON — no markdown, no explanation. The JSON must match this schema exactly:
{
  "rfx_id": "RFX-002",
  "category": "string",
  "issued_date": "YYYY-MM-DD",
  "deadline": "YYYY-MM-DD",
  "line_items": [
    {"id": 1, "description": "string", "spec": "string", "qty": integer, "unit": "string"}
  ],
  "questionnaire": ["string question 1", "string question 2", ...],
  "terms": {
    "currency": "INR",
    "payment_days": integer,
    "delivery_days": integer
  }
}

Rules:
- Generate realistic line items a procurement person would recognise.
- Include 5 questionnaire questions covering quality, lead time, compliance, location, and deviations.
- Default currency is INR unless the buyer says otherwise.
- issued_date is today; deadline is 9 days from today.
- rfx_id must be unique — use RFX-<timestamp> format if not specified.
"""


class RfxCreateRequest(BaseModel):
    description: str
    save: bool = False


@router.post("/create")
async def create_rfx(body: RfxCreateRequest):
    """Generate an RFx document from a natural-language buyer description."""
    today = date.today()
    deadline = today + timedelta(days=9)

    client = _make_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today is {today}. Deadline is {deadline}.\n\n"
                    f"Buyer description:\n{body.description}"
                ),
            }
        ],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if Claude wrapped it
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]

    rfx = json.loads(raw)

    if body.save:
        rfx_dir = Path("data/rfx")
        rfx_dir.mkdir(parents=True, exist_ok=True)
        rfx_id = rfx.get("rfx_id", "RFX-NEW")
        path = rfx_dir / f"{rfx_id}.json"
        path.write_text(json.dumps(rfx, indent=2, ensure_ascii=False))

    return {"rfx": rfx}


@router.get("/list")
async def list_rfx():
    """List all available RFx documents from data/rfx/."""
    rfx_dir = Path("data/rfx")
    if not rfx_dir.exists():
        return {"rfx_list": []}

    items = []
    for f in sorted(rfx_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append({
                "rfx_id": data.get("rfx_id", f.stem),
                "category": data.get("category", "—"),
                "deadline": data.get("deadline", "—"),
                "line_count": len(data.get("line_items", [])),
            })
        except Exception:
            pass
    return {"rfx_list": items}
