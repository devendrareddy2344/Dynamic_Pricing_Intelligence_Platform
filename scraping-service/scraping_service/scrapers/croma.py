import asyncio
import re
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers, get_mobile_headers
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth
from scraping_service.user_agents import random_ua, random_mobile_ua

_BLOCK_SIGNALS = [
    "captcha",
    "robot check",
    "temporarily blocked",
    "access denied",
    "unusual traffic",
    "blocked",
    "verify you are human",
]

# Croma uses dynamically generated class names but has stable data attributes
_CARD_SELECTORS = [
    ".product-item",
    ".cp-product",
    "li.product",
    "[data-product-id]",
    "div.product-tile",
    "div[class*='product-item']",
    "article[class*='product']",
]

_TITLE_SELECTORS = [
    ".product-title",
    ".pdt-name",
    "h3 a",
    "a.productTitle",
    "a[data-qa='product-title']",
    ".name",
    ".c-product__name",
    "a[class*='product-name']",
    "[class*='title'] a",
]

_PRICE_SELECTORS = [
    ".new-price",
    ".amount",
    ".pdpPrice",
    "[data-qa='product-price']",
    ".final-price",
    "span[class*='price']:not([class*='old']):not([class*='mrp'])",
    ".price",
]


def _is_croma_blocked(html: str) -> bool:
    text = (html or "").lower()
    return any(sig in text for sig in _BLOCK_SIGNALS) or len(text) < 1000


def _parse_croma_cards(
    soup: BeautifulSoup, product_name: str, url: str, latency_ms: float, method: str
) -> NormalisedOffer | None:
    cards = None
    for sel in _CARD_SELECTORS:
        found = soup.select(sel)
        if found:
            cards = found
            break

    if not cards:
        return None

    for card in cards[:12]:
        title_el = None
        for ts in _TITLE_SELECTORS:
            title_el = card.select_one(ts)
            if title_el:
                break

        price_el = None
        for ps in _PRICE_SELECTORS:
            price_el = card.select_one(ps)
            if price_el:
                break

        if not title_el or not price_el:
            continue

        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        if not href:
            first_a = card.select_one("a[href]")
            href = first_a.get("href", "") if first_a else ""
        if href.startswith("/"):
            href = "https://www.croma.com" + href

        raw = price_el.get_text(strip=True)
        if raw and not any(c in raw for c in "₹$€£"):
            raw = "₹" + raw
        p, _ = strip_currency(raw, "INR")
        if p is None:
            m = re.search(r"([\d,]+\.?\d*)", raw)
            if m:
                try:
                    p = float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
            else:
                continue
        if p <= 0:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="croma",
            price=p,
            currency="INR",
            product_title=title,
            product_url=href or url,
            in_stock=True,
            title_match_score=score,
            raw_price_text=raw,
            metadata={"latency_ms": round(latency_ms, 2), "method": method},
        )
    return None


async def scrape_croma(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Croma India search: Playwright-stealth → API → httpx fallback."""
    q = quote_plus(search_query[:120])
    url = f"https://www.croma.com/searchB?q={q}"
    t0 = time.perf_counter()

    # ── Stage 1: Playwright stealth (primary) ───────────────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-IN",
            {"width": 414, "height": 896},
            wait_selectors=_CARD_SELECTORS[:4] + [".product-title", ".new-price"],
            timeout=20000,
            proxy_url=None,
        )
        if html and not _is_croma_blocked(html):
            soup = BeautifulSoup(html, "lxml")
            offer = _parse_croma_cards(
                soup, product_name, url,
                (time.perf_counter() - t0) * 1000, "playwright"
            )
            if offer:
                return offer
    except Exception:
        pass

    # ── Stage 2: Croma internal search API ─────────────────────────────────
    api_url = "https://api.croma.com/searchservices/v1/search"
    api_params = {
        "currentPage": "0",
        "query": f"{search_query}:relevance",
        "fields": "FULL",
        "channel": "WEB",
        "channelCode": "",
        "spellOpt": "DEFAULT",
    }
    try:
        headers = get_browser_headers("croma")
        headers["User-Agent"] = random_ua()
        headers["Accept"] = "application/json, text/plain, */*"
        headers["Referer"] = url

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            trust_env=False,
            headers=headers,
            http2=False,
        ) as client:
            r = await client.get(api_url, params=api_params)

        if r.status_code == 200 and "error" not in r.text[:100].lower():
            data = r.json()
            products = data.get("products") or []
            for prod in products[:12]:
                title = (prod.get("name") or "").strip()
                price_obj = prod.get("price") or {}
                price_value = price_obj.get("value")
                raw_price = (
                    price_obj.get("formattedValue")
                    or (prod.get("mrp") or {}).get("formattedValue", "")
                )
                if price_value is None and raw_price:
                    price_value, _ = strip_currency(raw_price, "INR")
                href = prod.get("url", "")
                if href.startswith("/"):
                    href = "https://www.croma.com" + href
                if not title or price_value is None:
                    continue

                score = title_match_score(product_name, title)
                if not passes_validation(score):
                    continue

                return NormalisedOffer(
                    source="croma",
                    price=float(price_value),
                    currency="INR",
                    product_title=title,
                    product_url=href or url,
                    in_stock=bool(prod.get("stockFlag") is not None),
                    title_match_score=score,
                    raw_price_text=raw_price,
                    metadata={
                        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                        "method": "api",
                    },
                )
    except Exception:
        pass

    # ── Stage 3: httpx HTML fallback (single attempt, mobile UA) ────────────
    try:
        headers = get_mobile_headers("croma")
        headers["User-Agent"] = random_mobile_ua()
        headers.setdefault("Referer", "https://www.google.com/")

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            trust_env=False,
            headers=headers,
            http2=False,
        ) as client:
            r = await client.get(url)

        if r.status_code in {403, 429, 404}:
            return None
        if r.status_code != 200 or _is_croma_blocked(r.text):
            return None

        latency_ms = (time.perf_counter() - t0) * 1000
        soup = BeautifulSoup(r.text, "lxml")
        return _parse_croma_cards(soup, product_name, url, latency_ms, "httpx")

    except asyncio.TimeoutError:
        return None
    except Exception:
        return None
