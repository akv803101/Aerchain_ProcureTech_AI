"""POST /ingest and GET /ingest/status — ingestion pipeline routes."""

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.db.connection import get_db
from src.ingestion.pipeline import run_ingestion

router = APIRouter()


class IngestRequest(BaseModel):
    rfx_path: str = "data/rfx/RFX-001.json"
    vendor_dir: str = "data/vendor_responses"


@router.post("")
async def ingest(req: IngestRequest):
    """Run the full ingestion pipeline: extract → normalise → store."""
    try:
        summary = await run_ingestion(req.rfx_path, req.vendor_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return summary.model_dump()


@router.get("/status")
async def ingest_status(rfx_id: str = Query("RFX-001")):
    """Return ingestion status for an RFx: vendors processed, flags, ready flag."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT COUNT(DISTINCT vendor_id) FROM comparison WHERE rfx_id = ?",
            (rfx_id,),
        )
        vendors_processed = (await cur.fetchone())[0]

        cur = await db.execute(
            """SELECT DISTINCT vendor_id FROM comparison
               WHERE rfx_id = ? AND extraction_status = 'failed'""",
            (rfx_id,),
        )
        vendors_failed = [r["vendor_id"] for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT vendor_id, flags FROM comparison WHERE rfx_id = ?",
            (rfx_id,),
        )
        flag_rows = await cur.fetchall()

    total_flags: dict[str, int] = {}
    for row in flag_rows:
        for f in (json.loads(row["flags"]) if row["flags"] else []):
            total_flags[f] = total_flags.get(f, 0) + 1

    failure_detail = {
        vid: "0 lines extracted — check source file" for vid in vendors_failed
    }

    return {
        "rfx_id": rfx_id,
        "vendors_processed": vendors_processed,
        "vendors_failed": vendors_failed,
        "failure_detail": failure_detail,
        "total_flags": total_flags,
        "ready": vendors_processed > 0,
    }
