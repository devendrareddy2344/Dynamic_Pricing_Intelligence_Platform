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
from scraping_service.scrapers.headers import get_browser_headers, get_mobile_headers
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth
from scraping_service.user_agents import random_ua, random_mobile_ua
from scraping_service.proxies import get_proxy_dict, get_random_proxy


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
    ]
    return any(signal in text for signal in blocked_signals) or len(text) < 1200


def _parse_walmart_usd_price(raw: str) -> float | None:
    if not raw:
        return None
    raw = raw.strip()
    p_val, cur = strip_currency(raw, "USD")
    if p_val is not None:
        return p_val
    m = re.search(r"\$?\s?([\d,]+\.?\d*)", raw)
    if not m:
        return None
    price = float(m.group(1).replace(",", ""))
    return price if price < 10000 else None


def _extract_walmart_price_text(card) -> str:
    whole = card.select_one("span.price-characteristic")
    if whole:
        text = whole.get_text(strip=True)
        mantissa = card.select_one("span.price-mantissa")
        if mantissa:
            text = f"{text}.{mantissa.get_text(strip=True)}"
        return text

    group = card.select_one("span.price-group")
    if group:
        return group.get_text(" ", strip=True)

    price_el = (
        card.select_one("[data-automation-id='product-price']")
        or card.select_one(".f2, span.f-heading-5")
        or card.select_one("div.b_a")
        or card.select_one("span[data-automation-id='product-price']")
    )
    return price_el.get_text(" ", strip=True) if price_el else ""

    decimal_matches = re.findall(r"\$?\s*([0-9]{1,3}(?:[,][0-9]{3})*\.[0-9]{1,2})", raw)
    if decimal_matches:
        values = [float(match.replace(",", "")) for match in decimal_matches]
        price = min(values)
        return price if price < 10000 else None

    int_matches = re.findall(r"\$?\s*([0-9]{1,3}(?:[,][0-9]{3})*)", raw)
    if int_matches:
        values = [float(match.replace(",", "")) for match in int_matches]
        price = min(values)
        return price if price < 10000 else None

    return None


async def scrape_walmart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Walmart US search via httpx + UA rotation (faster than Playwright)."""
    q = quote_plus(search_query[:120])
    url = f"https://www.walmart.com/search?q={q}"
    t0 = time.perf_counter()
    
    # Try httpx with mobile headers first
    headers = get_mobile_headers("walmart")
    headers["User-Agent"] = random_mobile_ua()
    headers.setdefault("Referer", "https://www.google.com/")
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
            r = await client.get(url)
            
            if r.status_code == 200 and not _is_walmart_blocked(r.text, str(r.url)):
                latency_ms = (time.perf_counter() - t0) * 1000
                
                soup = BeautifulSoup(r.text, "lxml")
                cards = (
                    soup.select("[data-item-id]")
                    or soup.select("div[class*='search-result']")
                    or soup.select(".search-result-gridview-item")
                    or soup.select("div[data-testid='item-stack']")
                    or soup.select("div.search-result-gridview-item")
                )
                
                for card in cards[:12]:
                    title_el = (
                        card.select_one("[data-automation-id='product-title']")
                        or card.select_one("span.lh-title, span.f-heading-3")
                        or card.select_one("a.b_a.b_g")
                        or card.select_one("span[data-automation-id='product-title']")
                    )
                    if not title_el:
                        continue
                    
                    title = title_el.get_text(" ", strip=True)
                    href = card.select_one("a").get("href") if card.select_one("a") else ""
                    if href.startswith("/"):
                        href = "https://www.walmart.com" + href
                    
                    raw = _extract_walmart_price_text(card)
                    p_val = _parse_walmart_usd_price(raw)
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
                        metadata={"latency_ms": latency_ms, "method": "httpx"},
                    )
    except Exception:
        pass
    
    # Fallback: use Playwright if Walmart redirects to blocked page
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-US",
            {"width": 414, "height": 896},
            wait_selectors=["[data-item-id]", "div[class*='search-result']", ".search-result-gridview-item", "div[data-testid='item-stack']", "span.price-group", "span.price-characteristic", "a[aria-label*='Dell']"],
            timeout=10000,
        )
    except Exception:
        html = None

    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    cards = (
        soup.select("[data-item-id]")
        or soup.select("div[class*='search-result']")
        or soup.select(".search-result-gridview-item")
        or soup.select("div[data-testid='item-stack']")
        or soup.select("div.search-result-gridview-item")
    )
    for card in cards[:12]:
        title_el = (
            card.select_one("[data-automation-id='product-title']")
            or card.select_one("span.lh-title, span.f-heading-3")
            or card.select_one("a.b_a.b_g") or card.select_one("span[data-automation-id='product-title']")
        )
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        href = card.select_one("a").get("href") if card.select_one("a") else ""
        if href.startswith("/"):
            href = "https://www.walmart.com" + href
        raw = _extract_walmart_price_text(card)
        p_val = _parse_walmart_usd_price(raw)
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
            metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "playwright"},
        )

    return None

