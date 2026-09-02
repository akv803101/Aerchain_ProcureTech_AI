"""Intent router — classifies user messages and dispatches to the right handler.

No LLM involved: uses keyword + regex matching for speed and determinism.
"""

import re
from enum import Enum, auto


class Intent(Enum):
    INGEST = auto()      # "ingest vendors", "process files"
    RE_EXTRACT = auto()  # "re-extract vendor_d", "check again", "wrong price"
    CHART = auto()       # "chart", "plot", "visualise", "graph"
    QUERY = auto()       # "compare prices", "who is cheapest", "flag summary"
    EXPORT = auto()      # "export", "download", "generate report"
    RFX_BUILD = auto()   # "create rfx", "build rfx"
    STATUS = auto()      # "status", "ready", "summary"
    UNKNOWN = auto()


_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    (Intent.INGEST,      re.compile(r"\b(ingest|process|load|import|upload)[\w\s]*(vendor|file|response|quote)\b", re.I)),
    (Intent.RE_EXTRACT,  re.compile(r"\b(re[\s\-]?extract|reread|re[\s\-]?run|check\s+again|wrong\s+price|missed|update\s+extraction|extract\s+again)\b", re.I)),
    (Intent.CHART,       re.compile(r"\b(chart|plot|graph|visuali[sz]e?|bar\s+chart|pie\s+chart|show\s+me\s+a\s+chart)\b", re.I)),
    (Intent.EXPORT,    re.compile(r"\b(export|download|generate|save|produce)[\w\s]*(excel|xlsx|report|comparison|file)\b", re.I)),
    (Intent.RFX_BUILD, re.compile(r"\b(create|build|new|make|generate)[\w\s]*(rfx|rfq|request[\s\-]for[\s\-]quot)\b", re.I)),
    (Intent.STATUS,    re.compile(r"\b(status|ready|health|how many|how much|overview|check)\b", re.I)),
    # Query is the broadest — matched last
    (Intent.QUERY,     re.compile(
        r"\b(compare|cheapest|cheapest|lowest|highest|best|worst|rank|who|which|what|show|list|give|flag|confidence|price|cost|freight|discount|iso|lead\s*time|rejection|questionnaire)\b",
        re.I,
    )),
]


def classify(message: str) -> Intent:
    """Return the Intent for a user message using keyword pattern matching."""
    for intent, pattern in _PATTERNS:
        if pattern.search(message):
            return intent
    return Intent.UNKNOWN


class Orchestrator:
    """Routes a user message to the appropriate async handler and returns a response."""

    def __init__(self, rfx_id: str = "RFX-001"):
        self.rfx_id = rfx_id

    async def handle(self, message: str) -> dict:
        """Classify and dispatch a user message.

        Returns a dict with keys:
          - intent: str  — the classified intent name
          - response: str — the handler's text response
          - data: dict | None — structured data (tool results, summaries) when available
        """
        intent = classify(message)

        if intent == Intent.RE_EXTRACT:
            return await self._handle_re_extract(message)
        elif intent == Intent.CHART:
            return await self._handle_chart(message)
        elif intent == Intent.QUERY:
            return await self._handle_query(message)
        elif intent == Intent.INGEST:
            return await self._handle_ingest(message)
        elif intent == Intent.EXPORT:
            return await self._handle_export(message)
        elif intent == Intent.STATUS:
            return await self._handle_status(message)
        elif intent == Intent.RFX_BUILD:
            return {
                "intent": intent.name,
                "response": "RFx Builder is not yet connected. Please provide an RFx JSON file in data/rfx/ to proceed.",
                "data": None,
            }
        else:
            return {
                "intent": intent.name,
                "response": (
                    "I didn't recognise that request. You can ask me to:\n"
                    "• Compare vendor prices (e.g. 'Show lowest price for line 7')\n"
                    "• Summarise vendor quality (e.g. 'Flag summary for RFX-001')\n"
                    "• Export a comparison report (e.g. 'Export to Excel')\n"
                    "• Check system status"
                ),
                "data": None,
            }

    async def _handle_re_extract(self, message: str) -> dict:
        from src.agents.extraction_agent import run_extraction_agent
        answer = await run_extraction_agent(message, rfx_id=self.rfx_id)
        return {"intent": Intent.RE_EXTRACT.name, "response": answer, "data": None}

    async def _handle_chart(self, message: str) -> dict:
        """Build an inline Chart.js config from the DB and return it in data."""
        from src.db.connection import get_db

        msg_lower = message.lower()

        if any(w in msg_lower for w in ("scorecard", "performance", "quality", "confidence", "radar")):
            chart_type = "scorecard"
        elif any(w in msg_lower for w in ("coverage", "heatmap", "missing")):
            chart_type = "coverage_text"
        else:
            chart_type = "price"

        async with get_db() as db:
            if chart_type == "price":
                cursor = await db.execute(
                    """
                    SELECT vendor_id, SUM(price_inr) AS total
                    FROM comparison
                    WHERE rfx_id = ? AND price_inr IS NOT NULL
                    GROUP BY vendor_id ORDER BY total ASC
                    """,
                    (self.rfx_id,),
                )
                rows = await cursor.fetchall()
                labels = [r["vendor_id"] for r in rows]
                values = [round(r["total"], 2) for r in rows]
                chart_config = {
                    "type": "bar",
                    "data": {
                        "labels": labels,
                        "datasets": [{
                            "label": "Total Cost (₹)",
                            "data": values,
                            "backgroundColor": ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"],
                            "borderRadius": 6,
                        }],
                    },
                    "options": {
                        "responsive": True,
                        "plugins": {
                            "legend": {"display": False},
                            "title": {"display": True, "text": f"Total Cost by Vendor — {self.rfx_id}", "color": "#e2e8f0"},
                        },
                        "scales": {
                            "x": {"ticks": {"color": "#a0aec0"}, "grid": {"color": "#2d3748"}},
                            "y": {"beginAtZero": True, "ticks": {"color": "#a0aec0"}, "grid": {"color": "#2d3748"}},
                        },
                    },
                }
                summary = f"Here's a bar chart of total quoted cost per vendor for {self.rfx_id}. {labels[0] if labels else '—'} is cheapest overall."

            elif chart_type == "scorecard":
                cursor = await db.execute(
                    """
                    SELECT vendor_id,
                           AVG(confidence) AS avg_conf,
                           COUNT(*) AS lines,
                           SUM(CASE WHEN price_inr IS NOT NULL THEN 1 ELSE 0 END) AS priced
                    FROM comparison WHERE rfx_id = ? GROUP BY vendor_id ORDER BY avg_conf DESC
                    """,
                    (self.rfx_id,),
                )
                rows = await cursor.fetchall()
                labels = [r["vendor_id"] for r in rows]
                conf_vals = [round((r["avg_conf"] or 0) * 100, 1) for r in rows]
                cov_vals = [round(r["priced"] / r["lines"] * 100, 1) if r["lines"] else 0 for r in rows]
                chart_config = {
                    "type": "bar",
                    "data": {
                        "labels": labels,
                        "datasets": [
                            {"label": "Avg Confidence (%)", "data": conf_vals, "backgroundColor": "#3b82f6", "borderRadius": 4},
                            {"label": "Price Coverage (%)", "data": cov_vals, "backgroundColor": "#10b981", "borderRadius": 4},
                        ],
                    },
                    "options": {
                        "responsive": True,
                        "plugins": {
                            "title": {"display": True, "text": f"Vendor Scorecard — {self.rfx_id}", "color": "#e2e8f0"},
                            "legend": {"labels": {"color": "#a0aec0"}},
                        },
                        "scales": {
                            "x": {"ticks": {"color": "#a0aec0"}, "grid": {"color": "#2d3748"}},
                            "y": {"beginAtZero": True, "max": 100, "ticks": {"color": "#a0aec0"}, "grid": {"color": "#2d3748"}},
                        },
                    },
                }
                summary = f"Vendor scorecard for {self.rfx_id}: confidence vs. price coverage across {len(labels)} vendor(s)."

            else:
                return {
                    "intent": "CHART",
                    "response": "Coverage heatmap is available in the Charts panel (top toolbar → Charts).",
                    "data": None,
                }

        return {
            "intent": "CHART",
            "response": summary,
            "data": {"type": "chart", "chart_config": chart_config},
        }

    async def _handle_query(self, message: str) -> dict:
        from src.agents.query_agent import run_query
        answer = await run_query(message, rfx_id=self.rfx_id)
        return {"intent": Intent.QUERY.name, "response": answer, "data": None}

    async def _handle_ingest(self, message: str) -> dict:
        from src.db.connection import comparison_table_is_empty
        if not await comparison_table_is_empty():
            return {
                "intent": Intent.INGEST.name,
                "response": "Vendor data is already ingested. Re-run the ingestion pipeline via POST /ingest if you want to reload.",
                "data": None,
            }
        return {
            "intent": Intent.INGEST.name,
            "response": "Use POST /ingest to run the ingestion pipeline. Pass rfx_path and vendor_dir in the request body.",
            "data": None,
        }

    async def _handle_export(self, message: str) -> dict:
        return {
            "intent": Intent.EXPORT.name,
            "response": "Use GET /export to download the comparison report as a 3-sheet Excel file.",
            "data": None,
        }

    async def _handle_status(self, message: str) -> dict:
        from src.db.connection import get_db
        async with get_db() as db:
            cur = await db.execute("SELECT COUNT(DISTINCT vendor_id) FROM comparison")
            vendors = (await cur.fetchone())[0]
            cur = await db.execute("SELECT COUNT(*) FROM comparison")
            total_lines = (await cur.fetchone())[0]
            cur = await db.execute("SELECT AVG(confidence) FROM comparison")
            avg_conf = (await cur.fetchone())[0]

        ready = vendors > 0
        response = (
            f"System ready: {vendors} vendor(s), {total_lines} line items ingested. "
            f"Average confidence: {avg_conf:.2f}." if ready
            else "No vendor data ingested yet. Run the ingestion pipeline first."
        )
        return {
            "intent": Intent.STATUS.name,
            "response": response,
            "data": {"vendors": vendors, "total_lines": total_lines, "avg_confidence": round(avg_conf or 0, 3)},
        }
