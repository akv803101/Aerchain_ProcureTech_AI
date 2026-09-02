"""GET /export — download the comparison Excel report."""

from fastapi import APIRouter, Query
from fastapi.responses import Response

from src.export.excel import build_excel

router = APIRouter()


@router.get("")
async def export_excel(rfx_id: str = Query("RFX-001", description="RFx identifier")):
    """Generate and return a 3-sheet Excel workbook for the given RFx."""
    data = await build_excel(rfx_id=rfx_id)
    filename = f"comparison_{rfx_id}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
