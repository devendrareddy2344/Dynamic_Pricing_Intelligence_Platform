"""
cache.py — Vision result cache backed by Redis.

Redis is OPTIONAL. If the client is None or the connection fails,
all cache operations become no-ops so the app runs fine without Redis.
"""

from __future__ import annotations

import json
import logging

from typing import Any

logger = logging.getLogger(__name__)


async def get_cached_vision(r: Any, md5: str) -> dict[str, Any] | None:
    if r is None:
        return None
    try:
        raw = await r.get(f"vision:{md5}")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
    except Exception:
        logger.debug("Redis unavailable — vision cache miss for %s", md5)
        return None


async def set_cached_vision(
    r: Any, md5: str, payload: dict[str, Any], ttl_sec: int = 86400 * 30
) -> None:
    if r is None:
        return
    try:
        await r.setex(f"vision:{md5}", ttl_sec, json.dumps(payload))
    except Exception:
        logger.debug("Redis unavailable — vision cache write skipped for %s", md5)
