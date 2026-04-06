import os
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


def _is_croma_blocked(html: str) -> bool:
    text = (html or "").lower()
    blocked_signals = [
        "captcha",
        "robot check",
        "temporarily blocked",
        "access denied",
        "unusual traffic",
        "blocked",
    ]
    return any(signal in text for signal in blocked_signals) or len(text) < 800


async def scrape_croma(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Croma India search via httpx + UA rotation (faster than Playwright)."""
    q = quote_plus(search_query[:120])
    url = f"https://www.croma.com/searchB?q={q}"
    api_url = "https://api.croma.com/searchservices/v1/search"
    api_params = {
        "currentPage": "0",
        "query": f"{search_query}:relevance",
        "fields": "FULL",
        "channel": "WEB",
        "channelCode": "",
        "spellOpt": "DEFAULT",
    }
    t0 = time.perf_counter()

    # Try Croma's public API first; it is faster and avoids browser-blocking JS rendering.
    try:
        headers = get_browser_headers("croma")
        headers["User-Agent"] = random_ua()
        headers.setdefault("Referer", url)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
            r = await client.get(api_url, params=api_params)
            if r.status_code == 200 and "error" not in r.text.lower():
                data = r.json()
                products = data.get("products") or []
                for prod in products[:12]:
                    title = prod.get("name", "").strip()
                    price_value = prod.get("price", {}).get("value")
                    raw_price = prod.get("price", {}).get("formattedValue") or prod.get("mrp", {}).get("formattedValue", "")
                    if price_value is None and raw_price:
                        price_value, _ = strip_currency(raw_price, "INR")
                    href = prod.get("url", "")
                    if href.startswith("/"):
                        href = "https://www.croma.com" + href
                    if not title or price_value is None or not href:
                        continue

                    score = title_match_score(product_name, title)
                    if not passes_validation(score):
                        continue

                    return NormalisedOffer(
                        source="croma",
                        price=price_value,
                        currency="INR",
                        product_title=title,
                        product_url=href,
                        in_stock=bool(prod.get("stockFlag") is not None),
                        title_match_score=score,
                        raw_price_text=raw_price,
                        metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "api"},
                    )
            elif r.status_code in {403, 429}:
                pass
    except Exception:
        pass

    # Try multiple attempts with different UAs and request fingerprints if blocked
    # Exit quickly on consistent blocks
    consecutive_blocks = 0
    for attempt in range(1):  # Single fast attempt instead of 2
        headers = get_mobile_headers("croma")
        headers["User-Agent"] = random_mobile_ua()
        headers.setdefault("Referer", "https://www.google.com/")
        
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
                r = await client.get(url)
                
                # Check for bot challenge
                if _is_croma_blocked(r.text):
                    return None  # Skip to Playwright immediately
                
                if r.status_code in {403, 429}:
                    return None  # Skip to Playwright immediately
                if r.status_code == 404:
                    return None
                if r.status_code != 200:
                    return None
                
                latency_ms = (time.perf_counter() - t0) * 1000
                
                soup = BeautifulSoup(r.text, "lxml")
                cards = (
                    soup.select(".product-item")
                    or soup.select(".cp-product")
                    or soup.select("li.product")
                    or soup.select("[data-product-id]")
                    or soup.select("div.product-tile")
                )
                
                if not cards:
                    if attempt == 0:
                        continue
                    break  # Force Playwright path
                
                for card in cards[:12]:
                    title_el = (
                        card.select_one(".product-title") or card.select_one(".pdt-name")
                        or card.select_one("h3 a") or card.select_one("a.productTitle") or card.select_one("a[data-qa='product-title']")
                        or card.select_one(".name") or card.select_one(".c-product__name")
                    )
                    price_el = (
                        card.select_one(".new-price") or card.select_one(".amount")
                        or card.select_one(".pdpPrice") or card.select_one("span.price") or card.select_one("[data-qa='product-price']")
                        or card.select_one(".final-price") or card.select_one(".price")
                    )
                    
                    if not title_el or not price_el:
                        continue
                    
                    title = title_el.get_text(" ", strip=True)
                    href = title_el.get("href") or card.select_one("a").get("href") if card.select_one("a") else ""
                    if href.startswith("/"):
                        href = "https://www.croma.com" + href
                    
                    raw = price_el.get_text(strip=True)
                    p, _ = strip_currency(raw, "INR")
                    if p is None:
                        match = re.search(r"([\d,]+\.?\d*)", raw)
                        if match:
                            p = float(match.group(1).replace(",", ""))
                        else:
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
                        metadata={"latency_ms": latency_ms, "method": "httpx"},
                    )
        except asyncio.TimeoutError:
            continue
        except Exception:
            if attempt == 0:
                continue
    
    # Fallback: use Playwright if HTTP scraping is blocked by Croma
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-IN",
            {"width": 414, "height": 896},
            wait_selectors=[".product-item", ".cp-product", "li.product", "[data-product-id]", "div.product-tile"],
            timeout=10000,
        )
    except Exception:
        html = None

    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    cards = (
        soup.select(".product-item")
        or soup.select(".cp-product")
        or soup.select("li.product")
        or soup.select("[data-product-id]")
        or soup.select("div.product-tile")
    )
    for card in cards[:12]:
        title_el = (
            card.select_one(".product-title") or card.select_one(".pdt-name")
            or card.select_one("h3 a") or card.select_one("a.productTitle") or card.select_one("a[data-qa='product-title']")
            or card.select_one(".name") or card.select_one(".c-product__name")
        )
        price_el = (
            card.select_one(".new-price") or card.select_one(".amount")
            or card.select_one(".pdpPrice") or card.select_one("span.price") or card.select_one("[data-qa='product-price']")
            or card.select_one(".final-price") or card.select_one(".price")
        )
        
        if not title_el or not price_el:
            continue
        
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or card.select_one("a").get("href") if card.select_one("a") else ""
        if href.startswith("/"):
            href = "https://www.croma.com" + href
        
        raw = price_el.get_text(strip=True)
        p, _ = strip_currency(raw, "INR")
        if p is None:
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
            metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "playwright"},
        )

    return None

