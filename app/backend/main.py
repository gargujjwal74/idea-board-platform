"""Idea Board API."""
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("idea-board")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://ideas:ideas@localhost:5432/ideas")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id         SERIAL PRIMARY KEY,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

pool: ConnectionPool | None = None


class IdeaIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class IdeaOut(BaseModel):
    id: int
    content: str
    created_at: datetime


def _init_pool(retries: int = 30, delay: float = 2.0) -> ConnectionPool:
    """Open the pool and create the schema, retrying while the DB comes up."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            p = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}, open=True)
            p.wait(timeout=5)
            with p.connection() as conn:
                conn.execute(SCHEMA)
            log.info("database ready")
            return p
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("database not ready (attempt %d/%d): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError("database unavailable") from last_exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    pool = _init_pool()
    yield
    pool.close()


app = FastAPI(title="Idea Board API", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: the database is reachable."""
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        log.error("readiness failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready"}


@app.get("/api/ideas", response_model=list[IdeaOut])
def list_ideas():
    with pool.connection() as conn:
        return conn.execute("SELECT id, content, created_at FROM ideas ORDER BY id DESC").fetchall()


@app.post("/api/ideas", response_model=IdeaOut, status_code=201)
def create_idea(idea: IdeaIn):
    with pool.connection() as conn:
        row = conn.execute(
            "INSERT INTO ideas (content) VALUES (%s) RETURNING id, content, created_at",
            (idea.content.strip(),),
        ).fetchone()
    log.info("idea created id=%s", row["id"])
    return row
