"""FastAPI application entry-point for the Aerchain procurement AI."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from src.db.connection import init_db
from api.routes import chat, data, export, ingest, rfx


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    key = os.getenv("ANTHROPIC_API_KEY", "")
    wsid = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
    print(f"[startup] ANTHROPIC_API_KEY present={bool(key)} len={len(key)}", flush=True)
    print(f"[startup] ANTHROPIC_WORKSPACE_ID present={bool(wsid)} val={wsid[:12] if wsid else 'NOT SET'}", flush=True)
    yield


app = FastAPI(
    title="Aerchain Procurement AI",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
app.include_router(chat.router,   prefix="/chat",   tags=["chat"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(data.router,   prefix="/data",   tags=["data"])
app.include_router(rfx.router,    prefix="/rfx",    tags=["rfx"])

app.mount("/", StaticFiles(directory="api/static", html=True), name="static")
