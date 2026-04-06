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

# Flipkart obfuscates its CSS class names and changes them periodically.
# We use a broad multi-selector fallback strategy layered Playwright-first.

_BLOCK_SIGNALS = [
    "robot check",
    "suspicious activity",
    "blocked",
    "forbidden",
    "access denied",
    "captcha",
]

# Current Flipkart selectors (updated 2025-04).  Flipkart obfuscates class names
# so we keep multiple alternatives ordered from most-specific to least-specific.
_CARD_SELECTORS = [
    "div[data-id]",          # Most stable — present on list pages
    "div._1YokD2._3Mn1Gg",   # Horizontal list cards
    "div._1AtVbE",           # Grid wrapper
    "div.yNoU89",            # Alternative grid wrapper
    "div._13oc-S",           # Another grid variant
    "div._2kHMtA",           # Newer format
    "a._1fQZEK",             # Direct anchor card
    "div.cPHDOP",            # 2024 rebranded class
    "div.slAVV4",            # 2025 class seen in wild
]

_TITLE_SELECTORS = [
    "a.s1Q9rs",              # Standard desktop title link
    "a.IRpwTe",              # Mobile title link
    "a.WKTcLC",              # 2025 title link variant
    "div.KzDlHZ a",          # Nested title
    "div._4rR01T",           # Legacy title div
    "h2 a",                  # Generic heading link
    "a[title]",              # Any anchor with title attr
]

_PRICE_SELECTORS = [
    "div.Nx9Z0j",            # Primary price (2024+)
    "div.Nx9bqj",            # Alternate
    "div._30jeq3",           # Legacy desktop price
    "span._30jeq3",          # Legacy span price
    "div._25b18c",           # Older list price
    "div.hl05eU div.Nx9Z0j", # Nested price wrapper
    "span.a-size-base",      # Truly generic fallback
]


def _parse_flipkart_cards(
    soup: BeautifulSoup, product_name: str, url: str, latency_ms: float, method: str
) -> NormalisedOffer | None:
    """Parse product cards from Flipkart HTML and return the first valid offer."""
    cards = None
    for sel in _CARD_SELECTORS:
        found = soup.select(sel)
        if found:
            cards = found
            break

    if not cards:
        return None

    for card in cards[:15]:
        title_el = None
        for sel in _TITLE_SELECTORS:
            title_el = card.select_one(sel)
            if title_el:
                break

        price_el = None
        for sel in _PRICE_SELECTORS:
            price_el = card.select_one(sel)
            if price_el:
                break

        if not title_el or not price_el:
            continue

        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = "https://www.flipkart.com" + href

        raw = price_el.get_text(strip=True)
        # Strip currency symbol prefix that Flipkart sometimes omits
        if raw and not any(c in raw for c in "₹$€£"):
            raw = "₹" + raw
        p, _ = strip_currency(raw, "INR")
        if p is None or p <= 0:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="flipkart",
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


async def scrape_flipkart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Flipkart India search via Playwright-stealth (primary) + httpx (fallback)."""
    q = quote_plus(search_query[:120])
    url = f"https://www.flipkart.com/search?q={q}"
    t0 = time.perf_counter()

    # ── Stage 1: Playwright stealth (primary — Flipkart heavily blocks plain HTTP) ──
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-IN",
            {"width": 414, "height": 896},
            wait_selectors=_CARD_SELECTORS[:4] + ["div._4rR01T", "a.s1Q9rs"],
            timeout=20000,
            proxy_url=None,
        )
        if html:
            soup = BeautifulSoup(html, "lxml")
            offer = _parse_flipkart_cards(
                soup, product_name, url,
                (time.perf_counter() - t0) * 1000, "playwright"
            )
            if offer:
                return offer
    except Exception:
        pass

    # ── Stage 2: httpx fallback (mobile then desktop UA) ────────────────────
    for ua_fn, header_fn in [
        (random_mobile_ua, get_mobile_headers),
        (random_ua, get_browser_headers),
    ]:
        headers = header_fn("flipkart")
        headers["User-Agent"] = ua_fn()
        headers.setdefault("Referer", "https://www.google.com/")

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                trust_env=False,
                headers=headers,
                http2=False,
            ) as client:
                r = await client.get(url)

            if r.status_code in {403, 429}:
                continue
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                continue

            text_lower = r.text.lower()
            if any(sig in text_lower for sig in _BLOCK_SIGNALS):
                continue

            latency_ms = (time.perf_counter() - t0) * 1000
            soup = BeautifulSoup(r.text, "lxml")
            offer = _parse_flipkart_cards(
                soup, product_name, url, latency_ms, "httpx"
            )
            if offer:
                return offer

        except asyncio.TimeoutError:
            continue
        except Exception:
            continue

    return None
