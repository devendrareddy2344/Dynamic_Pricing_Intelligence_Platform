from __future__ import annotations

import sys
import asyncio

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import hashlib
import io
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

# Add sibling service paths for discovery
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for service in ["ml-service", "scraping-service", "vision-service", "genai-service"]:
    _p = os.path.join(_root, service)
    if _p not in sys.path:
        sys.path.append(_p)

from datetime import UTC, datetime
from typing import Any, Dict

import redis.asyncio as redis
from PIL import Image
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Optional prometheus import
try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    CONTENT_TYPE_LATEST = None
    generate_latest = None

from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api_gateway.db import fetch_price_history, get_pool, insert_price_rows
from api_gateway.logging_config import setup_logging
from api_gateway import metrics as prom
from genai_service.recommender import stream_pricing_recommendation
from ml_service.analyser import analyse_prices
from scraping_service.normaliser import isolation_flag_prices
from scraping_service.orchestrator import enabled_site_count, run_scrapers_streaming
from vision_service.app import identify_product_from_image

logger = logging.getLogger(__name__)

SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_LOCK = asyncio.Lock()

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = redis.from_url(redis_url, decode_responses=False, socket_connect_timeout=2)
        await r.ping()
        app.state.redis = r
        logger.info("Redis connected at %s", redis_url)
    except Exception as e:
        logger.warning("Redis unavailable (%s) — running without cache/circuit-breaker", e)
        app.state.redis = None
    await get_pool()
    yield
    if app.state.redis is not None:
        await app.state.redis.aclose()



app = FastAPI(title="Dynamic Pricing Intelligence API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount frontend static files
# We mount this AFTER all specific API routes are defined or at the end
# but mount(..., html=True) is usually done at the end.
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(frontend_path):
    logger.info("Serving frontend from %s", frontend_path)
    # We use a special mount that handles SPA routing (redirecting 404s to index.html)
    @app.exception_handler(404)
    async def spa_fallback(request, exc):
        if not request.url.path.startswith("/api/"):
            return FileResponse(os.path.join(frontend_path, "index.html"))
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
else:
    logger.warning("Frontend path %s not found. API mode only.", frontend_path)


class VisionResponse(BaseModel):
    session_id: str
    product_name: str
    brand: str
    category: str
    key_specs: list[str]
    search_queries: list[str]
    confidence: float
    notes: str
    low_confidence_warning: bool
    product_hash: str
    cache_hit: bool


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(UTC).isoformat()}


@app.get("/metrics")
async def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/vision", response_model=VisionResponse)
async def vision_endpoint(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 10MB")
    if len(raw) < 32:
        raise HTTPException(400, "Invalid image")

    try:
        from PIL import Image

        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        raise HTTPException(400, "Corrupted or unsupported image") from None

    img = Image.open(io.BytesIO(raw))
    img.load()
    if img.format not in ("JPEG", "PNG", "WEBP", "MPO"):
        raise HTTPException(400, f"Unsupported format: {img.format}")

    session_id = str(uuid.uuid4())
    md5 = hashlib.md5(raw).hexdigest()

    def vision_metrics(name: str, data: dict[str, Any]) -> None:
        if name == "vision_done":
            prom.vision_latency.observe(float(data.get("latency_ms", 0)))
            if data.get("confidence") is not None:
                prom.vision_confidence.observe(float(data["confidence"]))

    t0 = time.perf_counter()
    try:
        out = await identify_product_from_image(
            raw,
            md5,
            app.state.redis,
            session_id,
            metrics_callback=vision_metrics,
        )
    except Exception as e:
        logger.exception("vision_failed: %s", repr(e))
        raise HTTPException(502, f"Vision identification failed: {type(e).__name__}: {e or repr(e)}") from e

    cache_hit = bool(out.pop("_cache_hit", False))
    product_hash = md5
    conf = float(out.get("confidence", 0))
    low = conf < 0.5

    async with SESSION_LOCK:
        SESSIONS[session_id] = {
            "vision": out,
            "product_hash": product_hash,
            "image_md5": md5,
            "created": time.time(),
        }

    prom.end_to_end_query_latency_ms.observe((time.perf_counter() - t0) * 1000)

    return VisionResponse(
        session_id=session_id,
        product_name=out.get("product_name", ""),
        brand=out.get("brand", ""),
        category=out.get("category", ""),
        key_specs=list(out.get("key_specs") or []),
        search_queries=list(out.get("search_queries") or []),
        confidence=conf,
        notes=out.get("notes", ""),
        low_confidence_warning=low,
        product_hash=product_hash,
        cache_hit=cache_hit,
    )


async def _emit_to_session(session_id: str, q: asyncio.Queue, ev: dict[str, Any]) -> None:
    await q.put(ev)


async def _run_pipeline(session_id: str, q: asyncio.Queue) -> None:
    meta = SESSIONS.get(session_id)
    if not meta:
        await q.put({"event": "error", "message": "unknown_session"})
        await q.put(None)
        return

    vision = meta["vision"]
    product_hash = meta["product_hash"]
    product_name = vision.get("product_name", "")
    queries = list(vision.get("search_queries") or [product_name])

    offers: list[dict[str, Any]] = []
    t_pipeline = time.perf_counter()
    # Shared live exchange rate — fetched once per pipeline run
    _exchange_rates: dict[str, float] = {}

    async def _get_rate(from_cur: str, to_cur: str = "INR") -> float:
        key = f"{from_cur}_{to_cur}"
        if key not in _exchange_rates:
            try:
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=5.0) as _cl:
                    r = await _cl.get(f"https://api.exchangerate-api.com/v4/latest/{from_cur}")
                    if r.status_code == 200:
                        _exchange_rates[key] = float(r.json()["rates"][to_cur])
                        return _exchange_rates[key]
            except Exception:
                pass
            # Fallback rates
            _exchange_rates[key] = {"USD": 83.5, "EUR": 90.0, "GBP": 105.0, "CAD": 62.0, "AUD": 55.0, "SGD": 63.0}.get(from_cur, 1.0)
        return _exchange_rates[key]

    first_price_logged = False

    async def emit(ev: dict[str, Any]) -> None:
        nonlocal first_price_logged
        prom.observe_scraper_event(ev)
        if ev.get("event") == "price_scraped":
            raw_price = float(ev["price"])
            currency = ev.get("currency", "INR") or "INR"
            # Normalise everything to INR
            if currency.upper() != "INR":
                rate = await _get_rate(currency.upper())
                inr_price = round(raw_price * rate, 2)
                ev = {**ev, "price": inr_price, "currency": "INR",
                      "metadata": {**(ev.get("metadata") or {}),
                                   "original_price": raw_price,
                                   "original_currency": currency,
                                   "exchange_rate": rate}}
            else:
                inr_price = raw_price
            offers.append(
                {
                    "source": ev["source"],
                    "price": inr_price,
                    "currency": "INR",
                    "review_count": ev.get("review_count"),
                    "seller_rating": ev.get("seller_rating"),
                    "product_name": ev.get("product_name"),
                    "product_url": ev.get("product_url"),
                    "title_match_score": ev.get("title_match_score"),
                }
            )
            if not first_price_logged:
                first_price_logged = True
                prom.end_to_end_query_latency_ms.observe((time.perf_counter() - t_pipeline) * 1000)
        await _emit_to_session(session_id, q, ev)


    await run_scrapers_streaming(session_id, queries, product_name, app.state.redis, emit)

    n_sites = max(enabled_site_count(), 1)
    prom.scraper_success_rate.set(min(1.0, len(offers) / n_sites))

    # Persist rows
    rows_to_insert: list[dict[str, Any]] = []
    prices_only = [o["price"] for o in offers]
    anoms = isolation_flag_prices(prices_only) if len(prices_only) >= 3 else [False] * len(prices_only)
    for i, o in enumerate(offers):
        rows_to_insert.append(
            {
                "session_id": session_id,
                "product_hash": product_hash,
                "product_name": product_name,
                "source": o["source"],
                "price": o["price"],
                "currency": o.get("currency") or os.environ.get("CURRENCY", "USD"),
                "product_title": o.get("product_name"),
                "product_url": o.get("product_url"),
                "seller_rating": o.get("seller_rating"),
                "review_count": o.get("review_count"),
                "in_stock": True,
                "title_match_score": o.get("title_match_score"),
                "is_anomaly": anoms[i] if i < len(anoms) else False,
                "cluster_tier": None,
                "metadata": {},
            }
        )
    if rows_to_insert:
        try:
            await insert_price_rows(rows_to_insert)
            prom.price_history_records_total.inc(len(rows_to_insert))
        except Exception as e:
            logger.exception("db_insert_failed %s", e)

    if offers:
        rate = sum(anoms) / max(len(anoms), 1)
        prom.price_anomaly_rate.set(rate)

    t_ml0 = time.perf_counter()
    ml_out = analyse_prices(offers)
    prom.ml_analysis_latency_ms.observe((time.perf_counter() - t_ml0) * 1000)

    await _emit_to_session(
        session_id,
        q,
        {
            "event": "analysis_ready",
            "session_id": session_id,
            "recommended_price": ml_out.get("recommended_price"),
            "price_range": ml_out.get("price_range"),
            "strategy": ml_out.get("strategy"),
            "competitive_score": ml_out.get("competitive_score"),
            "ml": ml_out,
        },
    )

    if not ml_out.get("ready"):
        await _emit_to_session(
            session_id,
            q,
            {"event": "genai_skipped", "reason": ml_out.get("reason", "insufficient_data")}
        )
        await q.put(None)
        return

    t_gen0 = time.perf_counter()
    first = True
    try:
        async for chunk in stream_pricing_recommendation(
            {"vision": vision, "product_hash": product_hash},
            offers,
            ml_out,
        ):
            if first:
                prom.genai_time_to_first_token_ms.set((time.perf_counter() - t_gen0) * 1000)
                first = False
            await _emit_to_session(
                session_id,
                q,
                {"event": "genai_token", "token": chunk},
            )
        await _emit_to_session(session_id, q, {"event": "genai_done"})
    except Exception as e:
        logger.exception("genai_failed")
        await _emit_to_session(session_id, q, {"event": "genai_error", "message": str(e)})
    await q.put(None)


@app.post("/api/v1/sessions/{session_id}/scrape")
async def start_scrape(session_id: str):
    async with SESSION_LOCK:
        if session_id not in SESSIONS:
            raise HTTPException(404, "session not found")
        if SESSIONS[session_id].get("queue"):
            return {"started": False, "message": "already running"}
        q: asyncio.Queue = asyncio.Queue()
        SESSIONS[session_id]["queue"] = q

    asyncio.create_task(_run_pipeline(session_id, q))
    return {"started": True, "session_id": session_id}


@app.get("/api/v1/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    async def gen():
        async with SESSION_LOCK:
            meta = SESSIONS.get(session_id)
            if not meta or not meta.get("queue"):
                for _ in range(60):
                    await asyncio.sleep(0.5)
                    async with SESSION_LOCK:
                        meta = SESSIONS.get(session_id)
                        if meta and meta.get("queue"):
                            break
                if not meta or not meta.get("queue"):
                    yield {"event": "error", "data": json.dumps({"message": "no queue; call POST scrape first"})}
                    return
            q = meta["queue"]

        while True:
            item = await q.get()
            if item is None:
                yield {"event": "done", "data": "{}"}
                break
            yield {"event": item.get("event", "message"), "data": json.dumps(item, default=str)}

    return EventSourceResponse(gen())


@app.get("/api/v1/history/{product_hash}")
async def history(product_hash: str, days: int = 30):
    try:
        rows = await fetch_price_history(product_hash, days=days)
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"product_hash": product_hash, "points": rows}


@app.get("/api/v1/observability/circuit-breakers")
async def cb_status():
    from scraping_service.circuit_breaker import circuit_status

    return await circuit_status(app.state.redis)


@app.get("/api/v1/observability/sessions")
async def sessions_debug():
    async with SESSION_LOCK:
        return {"count": len(SESSIONS), "ids": list(SESSIONS.keys())}


@app.get("/")
async def root():
    return PlainTextResponse("Dynamic Pricing Intelligence API — see /docs")
