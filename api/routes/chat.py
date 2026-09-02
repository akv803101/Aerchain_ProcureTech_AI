"""POST /chat and GET /chat/history — procurement chat routes."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.db.connection import get_db
from src.orchestrator import Orchestrator

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    rfx_id: str = "RFX-001"
    session_id: str = "default"


class ChatResponse(BaseModel):
    intent: str
    response: str
    data: dict | None = None


async def _save_message(session_id: str, role: str, content: str, intent: str | None = None) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO chat_history (session_id, role, content, intent) VALUES (?, ?, ?, ?)",
            (session_id, role, content, intent),
        )
        await db.commit()


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Route a natural-language procurement question and return an answer."""
    await _save_message(req.session_id, "user", req.message)

    orch = Orchestrator(rfx_id=req.rfx_id)
    result = await orch.handle(req.message)

    await _save_message(req.session_id, "assistant", result["response"], result.get("intent"))
    return ChatResponse(**result)


@router.get("/history")
async def chat_history(session_id: str = Query("default"), limit: int = Query(50)):
    """Return conversation history for a session."""
    async with get_db() as db:
        cursor = await db.execute(
            """SELECT role, content, intent, created_at
               FROM chat_history WHERE session_id = ?
               ORDER BY id ASC LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()

    return {
        "session_id": session_id,
        "messages": [
            {"role": r["role"], "content": r["content"], "intent": r["intent"]}
            for r in rows
        ],
    }
