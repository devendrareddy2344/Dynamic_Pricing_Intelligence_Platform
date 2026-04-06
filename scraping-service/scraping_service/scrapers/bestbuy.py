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
    ]
    url_blocked = "block" in str(url).lower() or "captcha" in str(url).lower()
    return any(signal in text for signal in blocked_signals) or url_blocked or len(text) < 1000


async def scrape_bestbuy(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Best Buy search via Playwright (more reliable for dynamic content)."""
    q = quote_plus(search_query[:120])
    url = f"https://www.bestbuy.com/site/searchpage.jsp?st={q}&intl=nosplash"
    t0 = time.perf_counter()
    
    # Try Playwright first (more reliable for Best Buy's dynamic content)
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-US",
            {"width": 414, "height": 896},
            wait_selectors=[
                "li.sku-item", 
                ".sku-item", 
                "[data-sku-id]", 
                "ol li",
                "[data-automation='product-item']",
                ".product-item",
                ".sku-container",
                "[data-testid*='product']"
            ],
            timeout=10000,
        )
    except Exception:
        html = None

    if html:
        soup = BeautifulSoup(html, "lxml")
        cards = (
            soup.select("li.sku-item")
            or soup.select(".sku-item")
            or soup.select("[data-sku-id]")
            or soup.select("ol li")
            or soup.select("[data-automation='product-item']")
            or soup.select(".product-item")
            or soup.select(".sku-container")
            or soup.select("[data-testid*='product']")
        )
        
        for card in cards[:12]:
            title_el = card.select_one(
                ".sku-title a, h4 a, span.sku-title, a[data-automation='product-title'], "
                ".product-title a, .product-name a, [data-automation*='title'] a, "
                ".sku-header a, .product-link"
            )
            price_el = card.select_one(
                ".priceView-hero-price__price, .priceView-customer-price span, div.priceView span, span.sr-price, [data-automation='product-price'], "
                ".price-current, .product-price, [data-automation*='price'], .sr-price, .price, .priceView-price span, .price-block"
            )
            
            if not title_el or not price_el:
                continue
            
            title = title_el.get_text(" ", strip=True)
            href = title_el.get("href") or (card.select_one("a").get("href") if card.select_one("a") else "")
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
                metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "playwright"},
            )
    
    # Fallback: try HTTP scraping with single fast attempt
    try:
        headers = get_mobile_headers("bestbuy")
        headers["User-Agent"] = random_mobile_ua()
        headers.setdefault("Referer", "https://www.google.com/")
        
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
            r = await client.get(url)
            
            # Check for bot challenge (early exit to use Playwright)
            if _is_bestbuy_blocked(r.text, str(r.url)):
                return None  # Let orchestrator try alternative queries
            
            if r.status_code in {403, 429}:
                return None  # Blocked by server
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                return None
            
            latency_ms = (time.perf_counter() - t0) * 1000
            
            soup = BeautifulSoup(r.text, "lxml")
            cards = (
                soup.select("li.sku-item")
                or soup.select(".sku-item")
                or soup.select("[data-sku-id]")
                or soup.select("ol li")
            )
            
            if not cards:
                return None
            
            for card in cards[:12]:
                title_el = card.select_one(".sku-title a, h4 a, span.sku-title, a[data-automation='product-title']")
                price_el = card.select_one(".priceView-hero-price__price, .priceView-customer-price span, div.priceView span, span.sr-price, [data-automation='product-price'], .price-current, .price, .priceView-price span, .price-block")
                
                if not title_el or not price_el:
                    continue
                
                title = title_el.get_text(" ", strip=True)
                href = title_el.get("href") or (card.select_one("a").get("href") if card.select_one("a") else "")
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
                    metadata={"latency_ms": latency_ms, "method": "httpx"},
                )
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass
    
    return None
