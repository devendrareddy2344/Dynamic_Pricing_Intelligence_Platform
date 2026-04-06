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


def _is_bestbuy_blocked(html: str, url: str) -> bool:
    text = (html or "").lower()
    blocked_signals = [
        "captcha",
        "robot check",
        "temporarily unavailable",
        "access denied",
        "forbidden",
        "unusual traffic",
        "verify you are human",
        "pardon our interruption",   # BestBuy-specific Akamai block page
    ]
    url_blocked = "block" in str(url).lower() or "captcha" in str(url).lower()
    # BestBuy pages always contain product JSON; if less than 2 KB it's a block page
    return any(signal in text for signal in blocked_signals) or url_blocked or len(text) < 2000


def _parse_bestbuy_cards(
    soup: BeautifulSoup, product_name: str, url: str, latency_ms: float, method: str
) -> NormalisedOffer | None:
    """Shared card parser for both Playwright and httpx HTML paths."""
    cards = (
        soup.select("li.sku-item")
        or soup.select(".sku-item")
        or soup.select("[data-sku-id]")
        or soup.select("[data-automation='product-item']")
        or soup.select(".product-item")
        or soup.select(".sku-container")
        or soup.select("[data-testid*='product']")
        # 2024+ layout
        or soup.select("div[class*='shop-sku-list-item']")
        or soup.select("article[class*='product']")
    )

    for card in cards[:12]:
        title_el = card.select_one(
            ".sku-title a, h4.sku-header a, span.sku-title, "
            "a[data-automation='product-title'], .product-title a, "
            ".product-name a, [data-automation*='title'] a, "
            ".sku-header a, a.product-link, h4 a, "
            # 2024 layout
            "h2 a, a[class*='product-title']"
        )
        price_el = card.select_one(
            ".priceView-hero-price span[aria-hidden='true'], "
            ".priceView-hero-price .sr-only, "
            ".priceView-customer-price span, "
            "div.priceView span, span.sr-price, "
            "[data-automation='product-price'], "
            ".price-current, .product-price, "
            "[data-automation*='price'], .sr-price, "
            ".priceView-price span, .price-block, "
            # 2024 layout
            "span[class*='price'], div[class*='price']"
        )

        if not title_el:
            title_el = card.select_one("a[href]")
        if not title_el:
            continue

        title = title_el.get_text(" ", strip=True)
        if len(title) < 5:
            continue

        raw = price_el.get_text(strip=True) if price_el else ""
        if not raw:
            import re
            txt = card.get_text(" ", strip=True)
            m = re.search(r"\$\s?([\d,]+\.?\d*)", txt)
            if m:
                raw = m.group(0)
            else:
                continue
        href = title_el.get("href") or ""
        if not href:
            first_a = card.select_one("a[href]")
            href = first_a.get("href", "") if first_a else ""
        if href.startswith("/"):
            href = "https://www.bestbuy.com" + href

        raw = price_el.get_text(strip=True)
        p_val, _ = strip_currency(raw, "USD")
        if p_val is None:
            m = re.search(r"\$?\s*([\d,]+\.?\d*)", raw)
            if m:
                p_val = float(m.group(1).replace(",", ""))
            else:
                continue

        if p_val <= 0:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="bestbuy",
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


async def scrape_bestbuy(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Best Buy search via Playwright-stealth (primary) + httpx (fallback).

    Best Buy uses Akamai Bot Manager, which is very aggressive, so Playwright
    with stealth is the primary path. The httpx fallback is provided because
    BestBuy occasionally serves unchallenged responses to desktop UAs.
    """
    q = quote_plus(search_query[:120])
    # intl=nosplash prevents the country-selector popup
    url = f"https://www.bestbuy.com/site/searchpage.jsp?st={q}&intl=nosplash"
    t0 = time.perf_counter()

    # ── Stage 1: Playwright stealth (primary) ───────────────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_ua(),           # Desktop UA works better on BestBuy
            "en-US",
            {"width": 1280, "height": 800},
            wait_selectors=[
                "li.sku-item",
                ".sku-item",
                "[data-sku-id]",
                "[data-automation='product-item']",
                ".product-item",
                "div[class*='shop-sku-list-item']",
            ],
            timeout=40000,
            proxy_url=None,
        )
    except Exception:
        html = None

    if html and not _is_bestbuy_blocked(html, url):
        soup = BeautifulSoup(html, "lxml")
        offer = _parse_bestbuy_cards(
            soup, product_name, url,
            (time.perf_counter() - t0) * 1000, "playwright"
        )
        if offer:
            return offer

    # ── Stage 2: httpx fallback (single attempt, desktop UA) ────────────────
    try:
        headers = get_browser_headers("bestbuy")
        headers["User-Agent"] = random_ua()
        headers.setdefault("Referer", "https://www.google.com/")

        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            trust_env=False,
            headers=headers,
            http2=False,
        ) as client:
            r = await client.get(url)

        if r.status_code in {403, 429, 404}:
            return None
        if r.status_code != 200:
            return None
        if _is_bestbuy_blocked(r.text, str(r.url)):
            return None

        latency_ms = (time.perf_counter() - t0) * 1000
        soup = BeautifulSoup(r.text, "lxml")
        return _parse_bestbuy_cards(soup, product_name, url, latency_ms, "httpx")

    except asyncio.TimeoutError:
        return None
    except Exception:
        return None
