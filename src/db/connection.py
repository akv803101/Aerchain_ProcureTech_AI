import os
import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path

# Prefer /data (Railway volume) only if the directory actually exists and is writable.
# Falls back to db/aerchain.db so the app never crashes on a missing mount.
def _resolve_db_path() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    if os.getenv("RAILWAY_ENVIRONMENT"):
        data_dir = Path("/data")
        if data_dir.exists() and os.access(data_dir, os.W_OK):
            return str(data_dir / "aerchain.db")
    local = Path("db/aerchain.db")
    local.parent.mkdir(parents=True, exist_ok=True)
    return str(local)

DATABASE_URL = _resolve_db_path()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    async with aiosqlite.connect(DATABASE_URL) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS comparison (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfx_id TEXT NOT NULL,
                vendor_id TEXT NOT NULL,
                line_id INTEGER NOT NULL,
                description TEXT,
                price_raw REAL,
                price_inr REAL,
                unit_raw TEXT,
                unit_normalized TEXT,
                currency_raw TEXT,
                confidence REAL,
                flags TEXT,
                flag_notes TEXT,
                source_file TEXT,
                page_ref TEXT,
                extraction_status TEXT,
                extracted_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS vendor_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfx_id TEXT NOT NULL,
                vendor_id TEXT NOT NULL,
                freight_inr REAL,
                freight_notes TEXT,
                freight_unquantified INTEGER DEFAULT 0,
                discount_condition TEXT,
                discount_pct REAL,
                source_file TEXT,
                extracted_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS questionnaire (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfx_id TEXT NOT NULL,
                vendor_id TEXT NOT NULL,
                iso_certified TEXT,
                rejection_rate REAL,
                lead_time_days INTEGER,
                manufacturing_location TEXT,
                deviations TEXT,
                quote_validity_days INTEGER,
                source_file TEXT,
                extracted_at TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                intent TEXT,
                created_at REAL DEFAULT (julianday('now'))
            )
        """)

        await db.commit()


async def comparison_table_is_empty() -> bool:
    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM comparison")
        row = await cursor.fetchone()
        return row[0] == 0
