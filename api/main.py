"""FastAPI application entry-point for the Aerchain procurement AI."""

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv(Path(__file__).parent.parent / ".env", override=False)

from src.db.connection import init_db, comparison_table_is_empty
from src.ingestion.pipeline import run_ingestion
from api.routes import chat, data, export, ingest, rfx


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if await comparison_table_is_empty():
        await run_ingestion("data/rfx/RFX-001.json", "data/vendor_responses")
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
