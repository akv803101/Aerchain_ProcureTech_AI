# Architecture Decision Records

## ADR-001 — Claude as the extraction model

**Context:** Vendor RFx responses arrive in four formats (PDF, Excel, Word, image). Each has different layout conventions; a rules-based parser would require per-vendor templates.

**Decision:** Use `claude-sonnet-4-6` for all extraction. Text files use the Messages API with document content; images use the vision API.

**Rationale:**
- Single model handles all four formats without per-vendor code.
- Claude follows a locked system prompt (the PRD Section 6 `EXTRACTION_PROMPT`) which never changes; context is added only via the user-turn message.
- Extraction outputs a strict `ExtractionOutput` schema (Pydantic), so downstream code never sees raw LLM text.

**Trade-off:** Each extraction call costs ~2–5 API seconds and tokens. For large RFx batches, parallelise `asyncio.gather` over vendor files rather than processing serially.

---

## ADR-002 — Flag-based confidence scoring

**Context:** LLM extraction is non-deterministic. We need a numeric confidence score per line item to downstream filter low-quality data.

**Decision:** Define a set of `ExtractionFlag` values (e.g. `LOW_LEGIBILITY`, `PRICE_MISSING`, `UNIT_MISMATCH`, `CURRENCY_CONVERTED`). Each flag carries a fixed penalty. Confidence is computed as:

```
confidence = max(0, 1.0 - sum(penalties for flags on this line))
```

**Rationale:**
- Fully deterministic given the flag set — no LLM judgement involved.
- Auditable: any analyst can inspect which flags reduced confidence and why.
- Calibrated via the eval suite (`tests/eval/test_extraction.py`): GT flags are the minimum required set; the scoring threshold is ≥ 1.8/2.0 per vendor.

**Trade-off:** Flag accuracy depends on Claude emitting the right flags. The eval suite uses subset matching (GT ⊆ extracted flags) to allow Claude to add additional flags without failing. Ground truth represents minimum required flags only.

---

## ADR-003 — LangGraph `create_react_agent` for the Query Agent

**Context:** Procurement questions like "Who is cheapest?" can require multiple DB queries (get_price_comparison + get_lowest_price + get_flag_summary). A single-turn prompt cannot do this.

**Decision:** Use LangGraph `create_react_agent` over 6 LangChain `@tool` functions. The agent runs a ReAct loop: `[Thought → Tool call → Observation → …]` until it produces a final answer.

**Rationale:**
- LangGraph handles tool-calling retries, token limits, and conversation history automatically.
- Tools are `async` so the agent never blocks the FastAPI event loop.
- The `create_react_agent` API is the minimal surface area — no custom graph nodes needed at this complexity level.

**Trade-off:** Every question that triggers tool calls costs 2–5 API round-trips. For latency-sensitive use cases, cache common queries (e.g. `get_flag_summary`) or pre-compute them at ingestion time.

---

## ADR-004 — Regex intent router (no LLM)

**Context:** Every user message hits the Orchestrator first. Routing decisions (INGEST / QUERY / EXPORT / STATUS) must be fast and deterministic.

**Decision:** Use ordered `re.compile` patterns matched against the user message. No LLM call on the hot path.

**Pattern priority:** INGEST → EXPORT → RFX_BUILD → STATUS → QUERY → UNKNOWN

**Rationale:**
- Sub-millisecond routing; no API cost.
- Deterministic for the same message — no hallucinated intent.
- The most specific intents (INGEST, EXPORT) are checked before the broad QUERY pattern.
- "Flag summary" correctly routes to QUERY because "flag" is in the QUERY pattern and "summary" was intentionally removed from the STATUS pattern (it caused misrouting).

**Trade-off:** Regex patterns require maintenance when new user phrasings emerge. Extend `_PATTERNS` in `src/orchestrator.py` as needed; the test suite covers 32 representative messages.

---

## ADR-005 — SQLite via `aiosqlite`

**Context:** Procurement comparison data is read-heavy, single-tenant, and bounded by the number of vendors × line items (typically < 10k rows per RFx).

**Decision:** Use SQLite with `aiosqlite` for async I/O compatible with FastAPI's event loop.

**Rationale:**
- Zero infrastructure — no separate database process needed.
- `aiosqlite` wraps SQLite in a thread pool so it doesn't block the event loop.
- Schema is three normalised tables: `comparison`, `vendor_terms`, `questionnaire`.
- Migrating to PostgreSQL later requires only changing `DATABASE_URL` and replacing `aiosqlite.connect` with an async PG driver.

**Trade-off:** SQLite has write serialisation (one writer at a time). For concurrent ingestion of multiple RFx, use a lightweight queue or a proper DB. For single-RFx workloads, this is not a concern.

---

## ADR-006 — Locked extraction prompt

**Context:** The PRD specifies the exact extraction prompt in Section 6. Any modification risks breaking the calibrated eval suite.

**Decision:** Store the locked system prompt as `EXTRACTION_PROMPT` in `src/ingestion/extractor.py`. Append contextual instructions only to the **user-turn** message (not the system prompt).

**Additions to user turn only:**
- `_rfx_context(rfx)` — RFx category, line descriptions, target unit
- `_EXTRACTION_RULES` — instructs Claude not to pre-normalise prices, clarifies UNIT_MISMATCH semantics
- Per-call context (e.g. "this is a degraded image, apply LOW_LEGIBILITY to all lines")

**Rationale:** System prompt is immutable across all vendors; user-turn additions are the only safe extension point. This decouples prompt engineering from eval calibration.

---

## ADR-007 — Ground truth evaluation strategy

**Context:** LLM extraction is non-deterministic. Strict equality on extracted flags fails when Claude adds an optional flag (e.g. `VALUE_IN_PROSE`) in some runs but not others.

**Decision:** Use **subset matching** for non-empty ground truth: `gt_flags ⊆ ext_flags`. Ground truth lists the minimum required flags. Strict equality only when GT flags is empty (to catch spurious flags on clean lines).

**Scoring threshold:** `price_pct + flag_pct ≥ 1.8` (out of 2.0 max) per vendor.

**Rationale:** The eval suite validates that required quality signals are always present, while permitting Claude to emit additional informational flags. This reflects production reality: a downstream filter on `PRICE_MISSING` should trigger whenever the flag is present, whether or not other flags also appear.

**Trade-off:** A vendor could emit many spurious flags and still pass the eval. Mitigated by the confidence scoring — extra flags reduce confidence and surfaced in the flag summary.
