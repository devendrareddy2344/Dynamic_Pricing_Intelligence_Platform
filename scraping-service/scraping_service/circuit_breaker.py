"""
circuit_breaker.py — per-site circuit breaker backed by Redis.

Redis is OPTIONAL. If the client is None or unreachable, all circuit-breaker
operations are silently skipped (circuits are always considered closed).
"""

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

CB_PREFIX = "cb:site:"
OPEN_TTL = int(os.environ.get("CIRCUIT_BREAKER_OPEN_SEC", "600"))


async def should_skip(redis: Any, site: str) -> tuple[bool, str | None]:
    if redis is None:
        return False, None
    try:
        key = f"{CB_PREFIX}{site}"
        raw = await redis.get(key)
        if not raw:
            return False, None
        opened_until = float(raw.decode() if isinstance(raw, bytes) else raw)
        if time.time() < opened_until:
            return True, "circuit_open"
        return False, None
    except Exception:
        logger.debug("Redis unavailable — circuit breaker skipped for %s", site)
        return False, None


async def record_failure(redis: Any, site: str) -> None:
    if redis is None:
        return
    try:
        count_key = f"{CB_PREFIX}{site}:failures"
        n = await redis.incr(count_key)
        if n == 1:
            await redis.expire(count_key, OPEN_TTL)
        if n >= 3:
            until = time.time() + OPEN_TTL
            await redis.set(f"{CB_PREFIX}{site}", str(until), ex=OPEN_TTL)
            await redis.delete(count_key)
    except Exception:
        logger.debug("Redis unavailable — failure record skipped for %s", site)


async def record_success(redis: Any, site: str) -> None:
    if redis is None:
        return
    try:
        await redis.delete(f"{CB_PREFIX}{site}:failures")
    except Exception:
        logger.debug("Redis unavailable — success record skipped for %s", site)


async def circuit_status(redis: Any) -> dict[str, dict[str, Any]]:
    """Return open circuits and reset ETA for dashboard."""
    if redis is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        async for k in redis.scan_iter(match=f"{CB_PREFIX}*", count=50):
            ks = k.decode() if isinstance(k, bytes) else k
            if ":failures" in ks:
                continue
            raw = await redis.get(ks)
            if not raw:
                continue
            site = ks.replace(CB_PREFIX, "")
            try:
                opened_until = float(raw.decode() if isinstance(raw, bytes) else raw)
            except (TypeError, ValueError):
                continue
            now = time.time()
            out[site] = {
                "open": now < opened_until,
                "resets_at_epoch": opened_until,
                "seconds_remaining": max(0.0, opened_until - now),
            }
    except Exception:
        logger.debug("Redis unavailable — circuit status unavailable")
    return out
