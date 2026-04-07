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


def _is_walmart_blocked(html: str, url: str) -> bool:
    text = (html or "").lower()
    blocked_signals = [
        "robot or human",
        "verify you are human",
        "unusual traffic",
        "are you a robot",
        "captcha",
        "blocked",
        "access denied",
        "bot traffic",
    ]
    return any(signal in text for signal in blocked_signals) or len(text) < 2000


def _parse_walmart_price(card) -> str:
    """Extract price text from a Walmart card using multiple strategies."""
    # Strategy 1: Structured whole+mantissa spans
    whole = card.select_one("span.price-characteristic")
    if whole:
        text = whole.get_text(strip=True)
        mantissa = card.select_one("span.price-mantissa")
        if mantissa:
            text = f"{text}.{mantissa.get_text(strip=True)}"
        return text

    # Strategy 2: price-group wrapper span
    group = card.select_one("span.price-group")
    if group:
        return group.get_text(" ", strip=True)

    # Strategy 3: data-automation attribute selectors (2024+ layout)
    for sel in [
        "[data-automation-id='product-price']",
        "span[data-automation-id='product-price']",
        "div[data-testid='price-wrap'] span",
        ".f2.b.black.lh-copy",
        "span.inline-flex.flex-column",
    ]:
        el = card.select_one(sel)
        if el:
            return el.get_text(" ", strip=True)

    # Strategy 4: Fallback generic text search
    txt = card.get_text(" ", strip=True)
    m = re.search(r"\$\s?([\d,]+\.?\d*)", txt)
    if m:
        return m.group(0)

    return ""


def _parse_price_value(raw: str) -> float | None:
    """Parse a USD price string into a float."""
    if not raw:
        return None
    p_val, _ = strip_currency(raw.strip(), "USD")
    if p_val is not None:
        return p_val if 0 < p_val < 10000 else None
    m = re.search(r"\$?\s?([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        price = float(m.group(1).replace(",", ""))
        return price if 0 < price < 10000 else None
    except ValueError:
        return None


def _parse_walmart_cards(
    soup: BeautifulSoup, product_name: str, url: str, latency_ms: float, method: str
) -> NormalisedOffer | None:
    """Shared card parsing logic for both Playwright and httpx paths."""
    cards = (
        soup.select("[data-item-id]")
        or soup.select("div[data-testid='item-stack']")
        or soup.select("div[class*='search-result-gridview-item']")
        or soup.select("div[class*='search-result']")
        # 2024 layout
        or soup.select("div[data-testid='list-view']")
        or soup.select("section[data-testid*='product']")
    )

    for card in cards[:12]:
        title_el = (
            card.select_one("[data-automation-id='product-title']")
            or card.select_one("span[data-automation-id='product-title']")
            or card.select_one("span.lh-title")
            or card.select_one("span.f-heading-3")
            # 2024 layout
            or card.select_one("a[link-identifier]")
            or card.select_one("span[class*='lh-copy'][class*='normal']")
            or card.select_one("a[href]")
        )
        if not title_el:
            continue

        title = title_el.get_text(" ", strip=True)
        if len(title) < 5:
            continue

        # Resolve product link safely
        first_a = card.select_one("a[href]")
        href = first_a.get("href", "") if first_a else ""
        if href.startswith("/"):
            href = "https://www.walmart.com" + href

        raw = _parse_walmart_price(card)
        p_val = _parse_price_value(raw)
        if p_val is None:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="walmart",
            price=p_val,
            currency="USD",
            product_title=title,
            product_url=href or url,
            in_stock=True,
            title_match_score=score,
            raw_price_text=raw,
            metadata={"latency_ms": round(latency_ms, 2), "method": method},
        )
    return None


async def scrape_walmart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Walmart US search via Playwright-stealth (primary) + httpx (fallback).

    Walmart uses PerimeterX bot protection; Playwright stealth is essential.
    """
    q = quote_plus(product_name[:80] if len(product_name) > 5 else search_query[:80])
    url = f"https://www.walmart.com/search?q={q}"
    t0 = time.perf_counter()

    # ── Stage 1: httpx mobile bypass (faster than Playwright) ────────────────
    headers = get_mobile_headers("walmart")
    headers["User-Agent"] = random_mobile_ua()
    headers.setdefault("Referer", "https://www.google.com/")

    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            trust_env=False,
            headers=headers,
            http2=False,
        ) as client:
            r = await client.get(url)

        if r.status_code == 200 and not _is_walmart_blocked(r.text, str(r.url)):
            latency_ms = (time.perf_counter() - t0) * 1000
            soup = BeautifulSoup(r.text, "lxml")
            offer = _parse_walmart_cards(soup, product_name, url, latency_ms, "httpx")
            if offer:
                return offer
    except Exception:
        pass

    # ── Stage 2: Playwright stealth (fallback) ───────────────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_ua(),
            "en-US",
            {"width": 1280, "height": 800},
            wait_selectors=[
                "[data-item-id]",
                "div[data-testid='item-stack']",
                "div[data-testid='list-view']",
                "[data-automation-id='product-title']",
                "span.lh-title",
            ],
            timeout=45000,
            proxy_url=None,
        )
        if html and not _is_walmart_blocked(html, url):
            soup = BeautifulSoup(html, "lxml")
            offer = _parse_walmart_cards(
                soup, product_name, url,
                (time.perf_counter() - t0) * 1000, "playwright"
            )
            if offer:
                return offer
            else:
                with open("failed_walmart.html", "w", encoding="utf-8") as f:
                    f.write(html)
        elif html:
            with open("blocked_walmart.html", "w", encoding="utf-8") as f:
                f.write(html)
    except Exception:
        pass

    return None
