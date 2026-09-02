"""Pure unit tests for the normaliser and confidence modules.

No LLM calls, no I/O, fully deterministic.
"""
import pytest

from src.ingestion.confidence import FLAG_PENALTIES, compute_confidence, flags_to_notes
from src.ingestion.detector import detect_format
from src.ingestion.normaliser import normalise_currency, normalise_unit


# ── normalise_currency ───────────────────────────────────────────────────────

def test_usd_to_inr():
    price, flags = normalise_currency(1.0, "USD")
    assert price == 83.5
    assert "CURRENCY_CONVERTED" in flags


def test_usd_sample_value():
    price, flags = normalise_currency(0.489, "USD")
    assert abs(price - 40.83) < 0.01
    assert "CURRENCY_CONVERTED" in flags


def test_inr_passthrough():
    price, flags = normalise_currency(42.0, "INR")
    assert price == 42.0
    assert flags == []


def test_none_price_currency():
    price, flags = normalise_currency(None, "USD")
    assert price is None
    assert flags == []


# ── normalise_unit ───────────────────────────────────────────────────────────

def test_per_box_passthrough():
    price, unit, flags = normalise_unit(42.0, "per box", "5-ply box 40x30x20cm")
    assert price == 42.0
    assert unit == "per box"
    assert flags == []


def test_per_100_units():
    price, unit, flags = normalise_unit(9800.0, "per 100 units", "7-ply heavy duty box")
    assert price == 98.0
    assert unit == "per box"
    assert flags == []


def test_per_100_pcs():
    price, unit, flags = normalise_unit(3950.0, "per 100 pcs", "archive box")
    assert price == 39.5
    assert unit == "per box"


def test_per_dozen():
    price, unit, flags = normalise_unit(120.0, "per dozen", "small box")
    assert abs(price - 10.0) < 0.01
    assert unit == "per box"


def test_kg_to_box_with_dims():
    # 5-ply 50×40×30cm: estimated weight ~0.80 kg → price ≈ 42 × 0.80 ≈ 33.6
    price, unit, flags = normalise_unit(42.0, "per kg", "5-ply box 50x40x30cm")
    assert price is not None
    assert abs(price - 33.6) < 2.0  # ±2 INR tolerance for weight estimate
    assert unit == "per box"
    assert "UNIT_INFERRED" in flags


def test_kg_no_dims_returns_none():
    price, unit, flags = normalise_unit(38.0, "per kg", "3-ply box")
    assert price is None
    assert unit == "per box"
    assert "UNIT_INFERRED" in flags


def test_none_price_unit():
    price, unit, flags = normalise_unit(None, "per box", "some box")
    assert price is None
    assert flags == []


def test_mm_dimensions_parsed():
    # 400x300x250mm — should parse and estimate weight
    price, unit, flags = normalise_unit(38.0, "per kg", "Archive box 400x300x250mm")
    assert price is not None  # mm converted to cm, then estimated
    assert "UNIT_INFERRED" in flags


# ── compute_confidence ───────────────────────────────────────────────────────

def test_confidence_clean():
    assert compute_confidence([]) == 1.0


def test_confidence_unit_mismatch_value_in_prose():
    score = compute_confidence(["UNIT_MISMATCH", "VALUE_IN_PROSE"])
    assert score == pytest.approx(0.65)


def test_confidence_low_legibility_currency_converted():
    score = compute_confidence(["LOW_LEGIBILITY", "CURRENCY_CONVERTED"])
    assert score == pytest.approx(0.70)


def test_confidence_price_missing():
    assert compute_confidence(["PRICE_MISSING"]) == pytest.approx(0.0)


def test_confidence_temporal_reference():
    score = compute_confidence(["TEMPORAL_REFERENCE"])
    assert score == pytest.approx(0.10)


def test_confidence_unit_mismatch_unit_inferred():
    # vendor_e kg lines: 1 - 0.25 - 0.15 = 0.60
    score = compute_confidence(["UNIT_MISMATCH", "UNIT_INFERRED"])
    assert score == pytest.approx(0.60)


def test_confidence_all_penalties_floor_at_zero():
    heavy = ["PRICE_MISSING", "PRICE_AMBIGUOUS", "UNIT_MISMATCH", "LOW_LEGIBILITY"]
    assert compute_confidence(heavy) == 0.0


def test_all_flags_in_penalties():
    known = {
        "PRICE_MISSING", "PRICE_AMBIGUOUS", "UNIT_INFERRED", "UNIT_MISMATCH",
        "CURRENCY_CONVERTED", "TEMPORAL_REFERENCE", "LINE_NOT_IN_RFX",
        "VALUE_IN_PROSE", "LOW_LEGIBILITY", "FREIGHT_UNQUANTIFIED", "EXTRACTION_FAILED",
    }
    assert known == set(FLAG_PENALTIES.keys())


def test_flags_to_notes_returns_messages():
    notes = flags_to_notes(["PRICE_MISSING", "LOW_LEGIBILITY"])
    assert "PRICE_MISSING" in notes
    assert "LOW_LEGIBILITY" in notes
    assert isinstance(notes["PRICE_MISSING"], str)


# ── detect_format ─────────────────────────────────────────────────────────────

def test_detect_xlsx():
    assert detect_format("vendor_a_response.xlsx") == "excel"


def test_detect_pdf():
    assert detect_format("vendor_b_response.pdf") == "pdf"


def test_detect_docx():
    assert detect_format("vendor_c_response.docx") == "docx"


def test_detect_jpg():
    assert detect_format("vendor_d_response.jpg") == "image"


def test_detect_txt():
    assert detect_format("vendor_e_response.txt") == "text"


def test_detect_png():
    assert detect_format("scan.png") == "image"


def test_detect_fallback_text():
    assert detect_format("unknown_file.xyz") == "text"
