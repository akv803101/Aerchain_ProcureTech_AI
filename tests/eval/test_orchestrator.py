"""Eval tests for the intent router and Orchestrator.

Covers:
 - classify(): deterministic keyword matching for all 6 intents
 - Orchestrator.handle(): routing to the right handler per intent,
   with run_query mocked to avoid LLM calls.
 - STATUS handler with a real (temp) SQLite DB.

All tests are fast; none make LLM calls.
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.orchestrator import Intent, classify, Orchestrator


# ── classify() ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message,expected", [
    # INGEST — checked first in pattern list
    ("ingest vendor responses",      Intent.INGEST),
    ("process vendor file",          Intent.INGEST),
    ("load all vendor quotes",       Intent.INGEST),
    ("import vendor response",       Intent.INGEST),
    ("upload vendor file",           Intent.INGEST),
    # EXPORT — second in pattern list
    ("export to excel",              Intent.EXPORT),
    ("download the comparison report", Intent.EXPORT),
    ("generate comparison xlsx",     Intent.EXPORT),
    ("save comparison file",         Intent.EXPORT),
    ("produce the report file",      Intent.EXPORT),
    # RFX_BUILD — third
    ("create a new rfx",             Intent.RFX_BUILD),
    ("build rfq for stationery",     Intent.RFX_BUILD),
    ("make a new rfx",               Intent.RFX_BUILD),
    ("generate rfx for office supplies", Intent.RFX_BUILD),
    # STATUS — fourth
    ("status",                       Intent.STATUS),
    ("system is ready",              Intent.STATUS),
    ("check system health",          Intent.STATUS),
    ("how many vendors ingested",    Intent.STATUS),
    ("give me an overview of the system", Intent.STATUS),
    # QUERY — broadest, checked last
    ("compare vendor prices for line 1",   Intent.QUERY),
    ("who has the cheapest price?",        Intent.QUERY),
    ("lowest price for line 7",            Intent.QUERY),
    ("show flag summary for all vendors",  Intent.QUERY),
    ("what is the confidence score?",      Intent.QUERY),
    ("list questionnaire responses",       Intent.QUERY),
    ("which vendor has the best lead time", Intent.QUERY),
    ("show freight and discount terms",    Intent.QUERY),
    ("rank vendors by price",              Intent.QUERY),
    ("give me price delta between vendor_a and vendor_b", Intent.QUERY),
    # UNKNOWN — no keyword match
    ("hello there",                  Intent.UNKNOWN),
    ("",                             Intent.UNKNOWN),
    ("random words xyz pqr",         Intent.UNKNOWN),
])
def test_classify(message, expected):
    assert classify(message) == expected, f"classify({message!r}) should be {expected.name}"


# ── Orchestrator.handle() routing ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_query_routes_to_agent():
    """QUERY message must reach run_query; intent returned is 'QUERY'."""
    orch = Orchestrator(rfx_id="RFX-001")
    with patch("src.agents.query_agent.run_query", new_callable=AsyncMock) as mock_rq:
        mock_rq.return_value = "vendor_b is cheapest at ₹900"
        result = await orch.handle("who has the cheapest price?")

    assert result["intent"] == "QUERY"
    assert "vendor_b" in result["response"]
    mock_rq.assert_awaited_once()
    _, kwargs = mock_rq.call_args
    assert kwargs.get("rfx_id") == "RFX-001" or mock_rq.call_args.args[1] == "RFX-001"


@pytest.mark.asyncio
async def test_handle_export_returns_api_hint():
    """EXPORT message must return a hint about GET /export."""
    orch = Orchestrator()
    result = await orch.handle("export to excel")
    assert result["intent"] == "EXPORT"
    assert "/export" in result["response"]


@pytest.mark.asyncio
async def test_handle_rfx_build_returns_placeholder():
    """RFX_BUILD message must return the 'not yet connected' placeholder."""
    orch = Orchestrator()
    result = await orch.handle("create a new rfx for pencils")
    assert result["intent"] == "RFX_BUILD"
    assert result["data"] is None
    assert "rfx" in result["response"].lower() or "RFx" in result["response"]


@pytest.mark.asyncio
async def test_handle_unknown_returns_help():
    """UNKNOWN message must return help text listing valid intents."""
    orch = Orchestrator()
    result = await orch.handle("hello random text xyz")
    assert result["intent"] == "UNKNOWN"
    assert result["data"] is None
    # Should suggest at least one action category
    assert any(kw in result["response"].lower() for kw in ["compare", "export", "flag", "status"])


@pytest.mark.asyncio
async def test_handle_status_empty_db(tmp_path, monkeypatch):
    """STATUS with an empty (freshly initialised) DB returns a 'no data' message."""
    import src.db.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DATABASE_URL", str(tmp_path / "test.db"))

    from src.db.connection import init_db
    await init_db()

    orch = Orchestrator()
    result = await orch.handle("check status")
    assert result["intent"] == "STATUS"
    assert result["data"]["vendors"] == 0
    assert "not" in result["response"].lower() or result["data"]["total_lines"] == 0


@pytest.mark.asyncio
async def test_handle_status_with_data(tmp_path, monkeypatch):
    """STATUS with rows in the DB reports the correct vendor and line counts."""
    import src.db.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DATABASE_URL", str(tmp_path / "test.db"))

    from src.db.connection import init_db, get_db
    await init_db()

    # Seed two vendors with one line each
    async with get_db() as db:
        await db.execute(
            "INSERT INTO comparison (rfx_id, vendor_id, line_id, price_inr, confidence, flags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("RFX-001", "vendor_a", 1, 1000.0, 0.9, "[]"),
        )
        await db.execute(
            "INSERT INTO comparison (rfx_id, vendor_id, line_id, price_inr, confidence, flags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("RFX-001", "vendor_b", 1, 900.0, 0.85, "[]"),
        )
        await db.commit()

    orch = Orchestrator(rfx_id="RFX-001")
    result = await orch.handle("status")
    assert result["intent"] == "STATUS"
    assert result["data"]["vendors"] == 2
    assert result["data"]["total_lines"] == 2
    assert result["data"]["avg_confidence"] > 0


@pytest.mark.asyncio
async def test_handle_ingest_empty_db_returns_api_hint(tmp_path, monkeypatch):
    """INGEST message on an empty DB returns the POST /ingest API hint."""
    import src.db.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DATABASE_URL", str(tmp_path / "test.db"))

    from src.db.connection import init_db
    await init_db()

    orch = Orchestrator()
    result = await orch.handle("ingest vendor responses")
    assert result["intent"] == "INGEST"
    assert "/ingest" in result["response"]


@pytest.mark.asyncio
async def test_handle_ingest_already_ingested(tmp_path, monkeypatch):
    """INGEST message when the DB already has data tells the user it's loaded."""
    import src.db.connection as conn_mod
    monkeypatch.setattr(conn_mod, "DATABASE_URL", str(tmp_path / "test.db"))

    from src.db.connection import init_db, get_db
    await init_db()

    async with get_db() as db:
        await db.execute(
            "INSERT INTO comparison (rfx_id, vendor_id, line_id, price_inr, confidence, flags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("RFX-001", "vendor_a", 1, 1000.0, 0.9, "[]"),
        )
        await db.commit()

    orch = Orchestrator()
    result = await orch.handle("ingest vendor file")
    assert result["intent"] == "INGEST"
    assert "already" in result["response"].lower() or "re-run" in result["response"].lower()
