import asyncio
import base64
import json
import logging
import os
import re
import time
from typing import Any

import httpx
import io
from PIL import Image

from vision_service.cache import get_cached_vision, set_cached_vision
from vision_service.prompt import VISION_SYSTEM, VISION_USER
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)

def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise

def _validate_payload(data: dict[str, Any]) -> dict[str, Any]:
    required = [
        "product_name",
        "brand",
        "category",
        "key_specs",
        "search_queries",
        "confidence",
        "notes",
    ]
    for k in required:
        if k not in data:
            raise ValueError(f"missing key: {k}")
    if not isinstance(data["key_specs"], list):
        data["key_specs"] = list(data["key_specs"]) if data["key_specs"] else []
    if not isinstance(data["search_queries"], list):
        data["search_queries"] = list(data["search_queries"]) if data["search_queries"] else []
    conf = float(data["confidence"])
    data["confidence"] = max(0.0, min(1.0, conf))
    return data

async def identify_product_from_image(
    image_bytes: bytes,
    image_md5: str,
    redis_client: Any,
    session_id: str,
    metrics_callback: Any | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment or .env file")
        
    model_name = os.environ.get("OPENROUTER_VISION_MODEL", "google/gemini-2.0-flash-exp:free")

    cached = await get_cached_vision(redis_client, image_md5)
    cache_hit = cached is not None
    t0 = time.perf_counter()

    if cached:
        logger.info(
            "vision_cache_hit",
            extra={
                "session_id": session_id,
                "event": "vision_identify",
                "metadata": {"cache_hit": True, "model_used": model_name},
            },
        )
        if metrics_callback:
            metrics_callback(
                "vision_done",
                {
                    "latency_ms": (time.perf_counter() - t0) * 1000,
                    "cache_hit": True,
                    "confidence": cached.get("confidence"),
                },
            )
        return {**cached, "_cache_hit": True}

    # Compress Image for swift transmission to OpenRouter
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    kb = len(image_bytes) / 1024.0

    max_size = 512
    if max(img.width, img.height) > max_size:
        img.thumbnail((max_size, max_size))

    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    base64_img = base64.b64encode(buffered.getvalue()).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Synycs Dynamic Pricing"
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"{VISION_SYSTEM}\n\n{VISION_USER}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_img}"
                        }
                    }
                ]
            }
        ]
    }

    # Ordered fallback list — tries each model until one succeeds
    FALLBACK_MODELS = [
        os.environ.get("OPENROUTER_VISION_MODEL", "openrouter/free"),
        "google/gemma-3-27b-it:free",
        "google/gemma-3-12b-it:free",
        "google/gemma-3-4b-it:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "qwen/qwen3.6-plus:free",
    ]

    text = None
    last_error = None
    used_model = model_name

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt_model in FALLBACK_MODELS:
            payload["model"] = attempt_model
            try:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in (429, 404):
                    logger.warning(
                        "vision_model_skip: %s (status %s) — trying next model",
                        attempt_model, resp.status_code
                    )
                    last_error = resp.text
                    continue
                if resp.status_code != 200:
                    logger.error("openrouter_error_debug: %s → %s", attempt_model, resp.text)
                resp.raise_for_status()
                used_model = attempt_model
                text = resp.json()["choices"][0]["message"]["content"]
                break
            except httpx.HTTPStatusError as e:
                logger.warning("vision_model_http_error: %s → %s", attempt_model, e)
                last_error = str(e)
                continue
            except Exception as e:
                logger.exception("vision_openrouter_error: %s", e)
                raise

    if text is None:
        raise RuntimeError(
            f"All vision models exhausted (rate-limited). Last error: {last_error}"
        )


    latency_ms = (time.perf_counter() - t0) * 1000

    try:
        data = _validate_payload(_parse_json_response(text))
    except Exception as e:
        logger.warning("vision_parse_failed: %s raw=%s", e, text[:500])
        data = {
            "product_name": "Unknown product",
            "brand": "Unknown",
            "category": "Unknown",
            "key_specs": [],
            "search_queries": ["product"],
            "confidence": 0.35,
            "notes": f"Failed to parse model output: {e}",
        }

    await set_cached_vision(redis_client, image_md5, data)

    logger.info(
        "vision_identify",
        extra={
            "session_id": session_id,
            "event": "vision_identify",
            "metadata": {
                "model_used": model_name,
                "image_size_kb": round(kb, 2),
                "product_identified": data.get("product_name"),
                "confidence_score": data.get("confidence"),
                "cache_hit": False,
            },
        },
    )

    if metrics_callback:
        metrics_callback(
            "vision_done",
            {
                "latency_ms": latency_ms,
                "cache_hit": cache_hit,
                "confidence": data.get("confidence"),
            },
        )

    return {**data, "_cache_hit": False}
