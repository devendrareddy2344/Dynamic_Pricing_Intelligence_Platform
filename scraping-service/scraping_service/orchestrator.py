import asyncio
import logging
import os
import random
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from scraping_service.circuit_breaker import record_failure, record_success, should_skip
from scraping_service.scrapers import SCRAPER_FUNCS

logger = logging.getLogger(__name__)

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


def enabled_site_count() -> int:
    return len(_enabled_sites())


def _enabled_sites() -> list[str]:
    raw = os.environ.get("ENABLED_SCRAPERS", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip() in SCRAPER_FUNCS]
    region = os.environ.get("REGION", "us").lower()
    if region == "in":
        return ["flipkart", "croma", "amazon", "walmart", "bestbuy"]
    return ["amazon", "walmart", "bestbuy", "target", "flipkart", "croma"]


def _primary_query(search_queries: list[str], product_name: str) -> str:
    if search_queries:
        return search_queries[0]
    return product_name


async def _run_one_site(
    site: str,
    search_query: str,
    product_name: str,
    session_id: str,
    redis_client: Any,
    timeout_sec: float,
    max_retries: int,
    emit: EmitFn,
) -> None:
    skip, reason = await should_skip(redis_client, site)
    if skip:
        await emit(
            {
                "event": "scraper_failed",
                "session_id": session_id,
                "source": site,
                "reason": reason or "CIRCUIT_OPEN",
                "retry_count": 0,
                "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
        return

    jitter_min = float(os.environ.get("SCRAPER_JITTER_MIN_SEC", "1"))
    jitter_max = float(os.environ.get("SCRAPER_JITTER_MAX_SEC", "4"))
    await asyncio.sleep(random.uniform(jitter_min, jitter_max))

    fn = SCRAPER_FUNCS.get(site)
    if not fn:
        await emit(
            {
                "event": "scraper_failed",
                "session_id": session_id,
                "source": site,
                "reason": "UNKNOWN_SITE",
                "retry_count": 0,
                "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
        return

    delay = 1.0
    last_err = "UNKNOWN"
    t_start = time.perf_counter()

    # Playwright-python requires ProactorEventLoop on Windows.
    # Uvicorn sometimes initializes a SelectorEventLoop before we can set the policy.
    # This helper runs the scraper in a separate thread with its own Proactor loop if needed.
    def _run_with_custom_loop(f, *args):
        new_loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(new_loop)
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return new_loop.run_until_complete(f(*args))
        finally:
            new_loop.close()

    for attempt in range(max_retries):
        try:
            # Detect if we need the thread workaround
            is_win = sys.platform == "win32"
            needs_proactor = site in ["flipkart", "croma", "walmart", "bestbuy"]
            current_loop = asyncio.get_running_loop()
            
            # Use 'type(current_loop).__name__' to avoid import issues with internal loop classes
            is_proactor = "Proactor" in type(current_loop).__name__

            if is_win and needs_proactor and not is_proactor:
                # Run Playwright in a dedicated thread to ensure it gets a Proactor loop
                # But enforce timeout on the thread call itself
                offer = await asyncio.wait_for(
                    asyncio.to_thread(_run_with_custom_loop, fn, search_query, product_name, session_id),
                    timeout=timeout_sec
                )
            else:
                coro = fn(search_query, product_name, session_id)
                offer = await asyncio.wait_for(coro, timeout=timeout_sec)

            latency_ms = (time.perf_counter() - t_start) * 1000
            if offer is None:
                raise RuntimeError("NO_OFFER")
            await record_success(redis_client, site)
            await emit(
                {
                    "event": "price_scraped",
                    "session_id": session_id,
                    "source": offer.source,
                    "product_name": offer.product_title,
                    "price": offer.price,
                    "currency": offer.currency,
                    "seller_rating": offer.seller_rating,
                    "review_count": offer.review_count,
                    "in_stock": offer.in_stock,
                    "product_url": offer.product_url,
                    "title_match_score": offer.title_match_score,
                    "scraped_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "latency_ms": round(latency_ms, 2),
                    "search_query_used": search_query,
                    "metadata": offer.metadata,
                }
            )
            logger.info(
                "scraper_ok",
                extra={
                    "session_id": session_id,
                    "event": "scrape",
                    "metadata": {
                        "site": site,
                        "search_query_used": search_query,
                        "price_found": offer.price,
                        "product_title_match_score": offer.title_match_score,
                        "latency_ms": latency_ms,
                    },
                },
            )
            return
        except asyncio.TimeoutError:
            last_err = "TIMEOUT"
        except httpx.HTTPError as e:
            last_err = f"HTTP_{type(e).__name__}"
        except Exception as e:
            last_err = type(e).__name__.upper()
            if str(e):
                last_err = f"{last_err}:{str(e)[:80]}"

        if attempt < max_retries - 1:
            await asyncio.sleep(delay)
            delay *= 2

    await record_failure(redis_client, site)
    latency_ms = (time.perf_counter() - t_start) * 1000
    await emit(
        {
            "event": "scraper_failed",
            "session_id": session_id,
            "source": site,
            "reason": last_err,
            "retry_count": max_retries,
            "failed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "latency_ms": round(latency_ms, 2),
        }
    )
    logger.warning(
        "scraper_fail",
        extra={
            "session_id": session_id,
            "event": "scrape_fail",
            "metadata": {
                "site": site,
                "search_query_used": search_query,
                "http_status_code": None,
                "price_found": None,
                "product_title_match_score": None,
                "latency_ms": latency_ms,
            },
        },
    )


async def run_scrapers_streaming(
    session_id: str,
    search_queries: list[str],
    product_name: str,
    redis_client: Any,
    emit: EmitFn,
) -> None:
    """Launch all enabled scrapers concurrently; `emit` receives SSE-style dicts.
    Tries multiple search queries in sequence if initial results are empty.
    """
    sites = _enabled_sites()
    raw_timeout_sec = float(os.environ.get("SCRAPER_TIMEOUT_SECONDS", "20"))
    timeout_sec = min(raw_timeout_sec, 45.0)
    max_retries = int(os.environ.get("SCRAPER_MAX_RETRIES", "1"))
    if timeout_sec >= 20.0 and max_retries > 1:
        max_retries = 1

    # Build a list of candidate queries (deduplicated)
    candidates = []
    seen = set()
    for q in search_queries:
        if q and q.lower() not in seen:
            candidates.append(q)
            seen.add(q.lower())
    if product_name and product_name.lower() not in seen:
        candidates.append(product_name)

    # Use only the top 2 candidates to avoid exploding request counts
    queries_to_try = candidates[:2]

    # If the per-site timeout is already long, don't multiply it by query rotation.
    if timeout_sec >= 20.0 and len(queries_to_try) > 1:
        queries_to_try = [queries_to_try[0]]

    async def _run_site_with_query_rotation(site: str):
        # Initial attempt with primary query
        for idx, q_text in enumerate(queries_to_try):
            success_box = {"ok": False}
            
            async def wrapped_emit(ev):
                if ev.get("event") == "price_scraped":
                    success_box["ok"] = True
                await emit(ev)

            await _run_one_site(
                site,
                q_text,
                product_name,
                session_id,
                redis_client,
                timeout_sec,
                max_retries,
                wrapped_emit,
            )
            
            if success_box["ok"]:
                return # Product found, stop rotating queries for this site

    tasks = [asyncio.create_task(_run_site_with_query_rotation(site)) for site in sites]
    await asyncio.gather(*tasks)
