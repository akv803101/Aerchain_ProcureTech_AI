"""Integration eval tests for the LangGraph Query Agent.

Seeds a temp SQLite DB with known synthetic data, then runs the agent
against it via run_query(). Asserts responses contain expected keywords.

Requires ANTHROPIC_API_KEY. All tests are skipped automatically when the key
is absent so CI without credentials stays green.

Synthetic dataset (rfx_id="TEST-QA"):
  vendor_a: line 1 = ₹1000.00 (conf 0.9), line 2 = ₹2000.00 (conf 0.9), ISO: Yes, lead 14 days
  vendor_b: line 1 =  ₹900.00 (conf 0.85), line 2 = ₹2200.00 (conf 0.85), ISO: No,  lead 21 days
  vendor_c: line 1 = ₹1100.00 (conf 0.7),  line 2 = ₹1800.00 (conf 0.7), ISO: Yes, lead 10 days

For line 1: vendor_b is cheapest (₹900).
For line 2: vendor_c is cheapest (₹1800).
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live agent tests",
)

RFX = "TEST-QA"

_COMPARISON_ROWS = [
    # (rfx_id, vendor_id, line_id, description, price_inr, confidence, flags)
    (RFX, "vendor_a", 1, "A4 paper ream",    1000.0, 0.90, "[]"),
    (RFX, "vendor_a", 2, "Ballpoint pens",   2000.0, 0.90, "[]"),
    (RFX, "vendor_b", 1, "A4 paper ream",     900.0, 0.85, "[]"),
    (RFX, "vendor_b", 2, "Ballpoint pens",   2200.0, 0.85, "[]"),
    (RFX, "vendor_c", 1, "A4 paper ream",    1100.0, 0.70, "[]"),
    (RFX, "vendor_c", 2, "Ballpoint pens",   1800.0, 0.70, "[]"),
]

_QUESTIONNAIRE_ROWS = [
    # (rfx_id, vendor_id, iso_certified, rejection_rate, lead_time_days, manufacturing_location)
    (RFX, "vendor_a", "Yes", 0.5, 14, "Mumbai"),
    (RFX, "vendor_b", "No",  1.2, 21, "Delhi"),
    (RFX, "vendor_c", "Yes", 0.8, 10, "Chennai"),
]

_TERMS_ROWS = [
    # (rfx_id, vendor_id, freight_inr, freight_notes, freight_unquantified, discount_condition, discount_pct)
    (RFX, "vendor_a", 500.0,  "flat rate",    0, "orders > 50000", 5.0),
    (RFX, "vendor_b", 0.0,    "free shipping",0, None,             None),
    (RFX, "vendor_c", 1000.0, "per shipment", 0, "orders > 30000", 3.0),
]


@pytest.fixture(autouse=True)
async def seeded_db(tmp_path, monkeypatch):
    """Initialise a temp SQLite DB and seed all three tables."""
    import src.db.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DATABASE_URL", str(tmp_path / "qa_test.db"))

    from src.db.connection import init_db, get_db
    await init_db()

    async with get_db() as db:
        await db.executemany(
            "INSERT INTO comparison (rfx_id, vendor_id, line_id, description, price_inr, confidence, flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            _COMPARISON_ROWS,
        )
        await db.executemany(
            "INSERT INTO questionnaire (rfx_id, vendor_id, iso_certified, rejection_rate, "
            "lead_time_days, manufacturing_location) VALUES (?, ?, ?, ?, ?, ?)",
            _QUESTIONNAIRE_ROWS,
        )
        await db.executemany(
            "INSERT INTO vendor_terms (rfx_id, vendor_id, freight_inr, freight_notes, "
            "freight_unquantified, discount_condition, discount_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
            _TERMS_ROWS,
        )
        await db.commit()

    yield


# ── Query Agent tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lowest_price_line1():
    """Agent correctly identifies vendor_b as cheapest for line 1."""
    from src.agents.query_agent import run_query
    answer = await run_query("Who has the lowest price for line 1?", rfx_id=RFX)
    assert isinstance(answer, str) and len(answer) > 0
    assert "vendor_b" in answer.lower(), f"Expected vendor_b in answer: {answer}"


@pytest.mark.asyncio
async def test_lowest_price_line2():
    """Agent correctly identifies vendor_c as cheapest for line 2."""
    from src.agents.query_agent import run_query
    answer = await run_query("Which vendor has the lowest price for line 2?", rfx_id=RFX)
    assert isinstance(answer, str) and len(answer) > 0
    assert "vendor_c" in answer.lower(), f"Expected vendor_c in answer: {answer}"


@pytest.mark.asyncio
async def test_price_comparison_all_vendors():
    """Price comparison response lists all three vendors."""
    from src.agents.query_agent import run_query
    answer = await run_query("Compare prices for all vendors on line 1", rfx_id=RFX)
    assert "vendor_a" in answer.lower()
    assert "vendor_b" in answer.lower()
    assert "vendor_c" in answer.lower()


@pytest.mark.asyncio
async def test_flag_summary_returns_data():
    """Flag summary query returns a non-empty response mentioning vendors."""
    from src.agents.query_agent import run_query
    answer = await run_query("Show the flag summary for all vendors", rfx_id=RFX)
    assert isinstance(answer, str) and len(answer) > 0
    # Should mention at least one vendor
    assert any(v in answer.lower() for v in ("vendor_a", "vendor_b", "vendor_c"))


@pytest.mark.asyncio
async def test_questionnaire_iso():
    """Questionnaire query identifies ISO-certified vendors."""
    from src.agents.query_agent import run_query
    answer = await run_query(
        "Which vendors are ISO certified according to the questionnaire?", rfx_id=RFX
    )
    # vendor_a and vendor_c are ISO certified; vendor_b is not
    assert "vendor_a" in answer.lower() or "vendor_c" in answer.lower(), (
        f"Expected ISO-certified vendors in answer: {answer}"
    )


@pytest.mark.asyncio
async def test_freight_terms():
    """Freight query returns freight information for vendors."""
    from src.agents.query_agent import run_query
    answer = await run_query("What are the freight terms for each vendor?", rfx_id=RFX)
    assert isinstance(answer, str) and len(answer) > 0
    assert any(
        kw in answer.lower() for kw in ("freight", "shipping", "₹", "free", "flat")
    ), f"Expected freight terms in answer: {answer}"


@pytest.mark.asyncio
async def test_price_delta_vendors():
    """Price delta query returns a percentage difference between two vendors."""
    from src.agents.query_agent import run_query
    answer = await run_query(
        "What is the price difference between vendor_a and vendor_b for line 1?", rfx_id=RFX
    )
    assert isinstance(answer, str) and len(answer) > 0
    # vendor_a=1000, vendor_b=900 → 11.11% cheaper for vendor_b
    assert any(c.isdigit() for c in answer), f"Expected numeric delta in answer: {answer}"


@pytest.mark.asyncio
async def test_response_is_string_not_empty():
    """Any sensible procurement question must return a non-empty string."""
    from src.agents.query_agent import run_query
    answer = await run_query("List all vendor IDs that have been quoted", rfx_id=RFX)
    assert isinstance(answer, str)
    assert len(answer.strip()) > 0
