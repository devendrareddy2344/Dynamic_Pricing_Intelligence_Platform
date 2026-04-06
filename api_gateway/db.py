"""
db.py — PostgreSQL backend using asyncpg.

Public API (unchanged so main.py needs no edits):
  - get_pool()               → creates / returns the asyncpg connection pool
  - insert_price_rows(rows)  → bulk INSERT into price_history
  - fetch_price_history(...) → SELECT from price_history
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse_dsn() -> str:
    """
    Always prefer individual PG_* env vars — avoids any URL percent-encoding
    issues with special characters (like @ in passwords).
    Falls back to parsing DATABASE_URL only when PG_* vars are absent.
    """
    # Individual vars take priority — no encoding/decoding issues
    if os.environ.get("PG_HOST") or os.environ.get("PG_USER"):
        from urllib.parse import quote
        host = os.environ.get("PG_HOST", "localhost")
        port = int(os.environ.get("PG_PORT", "5432"))
        user = os.environ.get("PG_USER", "dpuser")
        password = quote(os.environ.get("PG_PASSWORD", "dppass"))
        db = os.environ.get("PG_DATABASE", "dynamic_pricing")
        return f"postgresql://{user}:{password}@{host}:{port}/{db}"
    # Fallback: use DATABASE_URL directly
    url = os.environ.get("DATABASE_URL", "postgresql://dpuser:dppass@localhost:5432/dynamic_pricing")
    if url.startswith("postgresql://"):
        return url
    # Hard defaults
    return "postgresql://dpuser:dppass@localhost:5432/dynamic_pricing"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_pool() -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            dsn = _parse_dsn()
            logger.info("Connecting to PostgreSQL at %s", dsn.split('@')[1] if '@' in dsn else dsn)
            _pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=1,
                max_size=10,
                command_timeout=60,
            )
    return _pool


async def insert_price_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    now = datetime.now(timezone.utc)
    sql = """
        INSERT INTO price_history (
            session_id, product_hash, product_name, source, price, currency,
            product_title, product_url, seller_rating, review_count, in_stock,
            title_match_score, is_anomaly, cluster_tier, scraped_at, metadata
        ) VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10, $11,
            $12, $13, $14, $15, $16
        )
    """
    params = [
        (
            r["session_id"],
            r["product_hash"],
            r.get("product_name"),
            r.get("source"),
            r.get("price"),
            r.get("currency", "USD"),
            r.get("product_title"),
            r.get("product_url"),
            r.get("seller_rating"),
            r.get("review_count"),
            bool(r.get("in_stock", True)),
            r.get("title_match_score"),
            bool(r.get("is_anomaly", False)),
            r.get("cluster_tier"),
            now,
            json.dumps(r.get("metadata") or {}),
        )
        for r in rows
    ]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(sql, params)


async def fetch_price_history(product_hash: str, days: int = 30) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = """
        SELECT source, price, currency, scraped_at, product_title, session_id
        FROM price_history
        WHERE product_hash = $1
          AND scraped_at   >= $2
        ORDER BY scraped_at ASC
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, product_hash, cutoff)

    # scraped_at is a datetime object from Postgres — convert to ISO string
    out = []
    for r in rows:
        row = dict(r)
        if isinstance(row.get("scraped_at"), datetime):
            row["scraped_at"] = row["scraped_at"].isoformat()
        out.append(row)
    return out
