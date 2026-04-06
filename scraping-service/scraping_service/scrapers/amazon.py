import os
import json
import re
import asyncio
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers
from scraping_service.user_agents import random_ua
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth


def _parse_amazon_cards(
    soup: BeautifulSoup,
    product_name: str,
    base_url: str,
    tld: str,
    latency_ms: float,
    method: str,
) -> NormalisedOffer | None:
    """Shared card-parsing logic for both httpx and Playwright HTML paths."""
    items = (
        soup.select("[data-component-type='s-search-result']")
        or soup.select(".s-result-item[data-asin]")
        or soup.select("div[data-asin]")
        or soup.select(".s-card-container")
    )

    for it in items[:20]:
        # Skip sponsored/ad sentinel cards
        asin = it.get("data-asin", "").strip()
        if not asin:
            continue

        title_el = (
            it.select_one("h2 a span")
            or it.select_one("h2 span")
            or it.select_one(".a-size-medium.a-color-base.a-text-normal")
            or it.select_one(".a-size-base-plus.a-color-base.a-text-normal")
            or it.select_one(".a-size-medium")
            or it.select_one(".a-text-normal")
        )
        link_el = (
            it.select_one("h2 a")
            or it.select_one("a.a-link-normal[href*='/dp/']")
            or it.select_one(".a-link-normal[href]")
        )

        if not title_el or not link_el:
            continue

        title = title_el.get_text(" ", strip=True)
        href = link_el.get("href") or ""
        if href.startswith("/"):
            href = base_url + href

        # ── Price extraction (3 strategies) ──────────────────────────────────
        raw = ""

        # Strategy 1: Structured .a-price-whole / .a-price-fraction
        price_whole = it.select_one(".a-price-whole")
        price_frac = it.select_one(".a-price-fraction")
        if price_whole:
            whole_text = price_whole.get_text(strip=True).rstrip(".")
            # Strip trailing dot and commas
            whole_text = re.sub(r"[,.]", "", whole_text)
            if price_frac:
                frac_text = price_frac.get_text(strip=True)
                raw = f"{whole_text}.{frac_text}"
            else:
                raw = whole_text
            raw = (f"₹{raw}" if tld == "in" else f"${raw}")

        # Strategy 2: Screen-reader offscreen price (most reliable)
        if not raw:
            off = it.select_one(".a-offscreen")
            if off:
                raw = off.get_text(strip=True)

        # Strategy 3: Regex over the card's full text
        if not raw:
            price_txt = it.get_text(" ", strip=True)
            m = re.search(r"[₹\$]\s?[\d,]+\.?\d*", price_txt)
            if m:
                raw = m.group(0)
            else:
                continue

        p, detected_cur = strip_currency(raw, "INR" if tld == "in" else "USD")
        if p is None or p <= 0:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="amazon",
            price=p,
            currency=detected_cur,
            product_title=title,
            product_url=href,
            in_stock=True,
            title_match_score=score,
            raw_price_text=raw,
            metadata={"latency_ms": round(latency_ms, 2), "method": method},
        )
    return None


async def scrape_amazon(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Amazon search with robust UA rotation and anti-bot logic."""
    q = quote_plus(search_query[:200])

    # Region detection
    region = os.environ.get("REGION", "us").lower()
    tld = "in" if region == "in" else "com"
    base_url = f"https://www.amazon.{tld}"
    url = f"{base_url}/s?k={q}"

    t0 = time.perf_counter()

    # ── Stage 1: HTTP attempts (fast path) ──────────────────────────────────
    for attempt in range(3):
        headers = get_browser_headers("amazon")
        headers["User-Agent"] = random_ua()

        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=True,
                headers=headers,
                http2=False,
            ) as client:
                r = await client.get(url)

            text_lower = r.text.lower()
            # Amazon bot-signals
            if any(
                x in text_lower
                for x in [
                    "captcha",
                    "robot check",
                    "sorry! something went wrong",
                    "api-services-support@amazon.com",
                    "enter the characters you see",
                    "automated access",
                    "verify yourself",
                ]
            ):
                if attempt < 2:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                break  # fall through to Playwright

            if r.status_code == 404:
                return None
            if r.status_code != 200:
                if attempt < 2:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                break

            latency_ms = (time.perf_counter() - t0) * 1000
            soup = BeautifulSoup(r.text, "lxml")
            offer = _parse_amazon_cards(soup, product_name, base_url, tld, latency_ms, "httpx")
            if offer:
                return offer
            # Page loaded but no cards found — could be a CAPTCHA soft-block
            break

        except (asyncio.TimeoutError, httpx.HTTPError):
            if attempt < 2:
                await asyncio.sleep(1.5 ** attempt)
                continue
            break
        except Exception:
            break

    # ── Stage 2: Playwright stealth fallback ────────────────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_ua(),
            "en-IN" if tld == "in" else "en-US",
            {"width": 1280, "height": 800},
            wait_selectors=[
                "[data-component-type='s-search-result']",
                ".s-result-item[data-asin]",
                "div[data-asin]",
                ".a-price-whole",
                ".a-offscreen",
            ],
            timeout=40000,
            proxy_url=None,
        )
    except Exception:
        html = None

    if not html:
        return None

    latency_ms = (time.perf_counter() - t0) * 1000
    soup = BeautifulSoup(html, "lxml")
    return _parse_amazon_cards(soup, product_name, base_url, tld, latency_ms, "playwright")
