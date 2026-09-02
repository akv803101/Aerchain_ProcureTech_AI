from typing import Optional
from pydantic import BaseModel


class LineItemRaw(BaseModel):
    line_id: Optional[int] = None
    description: str = ""
    price: Optional[float] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    flags: list[str] = []
    flag_notes: str = ""


class ExtractionOutput(BaseModel):
    line_items: list[LineItemRaw] = []
    questionnaire: dict = {}
    freight: Optional[float] = None
    freight_notes: Optional[str] = None
    freight_unquantified: bool = False
    discount_condition: Optional[str] = None
    discount_pct: Optional[float] = None
    quote_validity_days: Optional[int] = None


class LineItemNormalized(BaseModel):
    vendor_id: str
    rfx_id: str
    line_id: Optional[int]
    description: str
    price_raw: Optional[float]
    price_inr: Optional[float]
    unit_raw: Optional[str]
    unit_normalized: Optional[str]
    currency_raw: Optional[str]
    confidence: float
    flags: list[str]
    flag_notes: dict
    source_file: str
    page_ref: Optional[str]
    extraction_status: str = "ok"


class IngestionSummary(BaseModel):
    vendors_processed: int
    vendors_failed: list[str]
    failure_detail: dict
    total_lines: int
    total_flags: dict
    ready: bool
