import base64
import os
import re
import time
import asyncio
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth
from scraping_service.user_agents import random_ua, random_mobile_ua

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


def _is_ebay_blocked(html: str, url: str) -> bool:
    text = (html or "").lower()
    blocked_signals = [
        "captcha",
        "robot check",
        "access denied",
        "temporarily unavailable",
        "blocked",
        "unusual activity",
    ]
    url_blocked = "block" in str(url).lower() or "captcha" in str(url).lower()
    return any(signal in text for signal in blocked_signals) or url_blocked or len(text) < 1000


async def _ebay_oauth_token(client: httpx.AsyncClient) -> str | None:
    cid = os.environ.get("EBAY_CLIENT_ID", "").strip()
    csec = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        return None
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    try:
        r = await client.post(
            _TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        return r.json().get("access_token")
    except Exception:
        return None


async def _browse_api_search(
    client: httpx.AsyncClient, token: str, q: str, product_name: str
) -> NormalisedOffer | None:
    mid = os.environ.get("EBAY_MARKETPLACE_ID", "EBAY_US")
    url = "https://api.ebay.com/buy/browse/v1/item_summary/search"
    try:
        r = await client.get(
            url,
            params={"q": q, "limit": 10},
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": mid,
            },
            timeout=12.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get("itemSummaries") or []
        for it in items:
            title = it.get("title") or ""
            price_obj = it.get("price") or {}
            val = price_obj.get("value")
            cur = price_obj.get("currency", "USD")
            if val is None:
                continue
            p = float(str(val).replace(",", ""))
            href = it.get("itemWebUrl") or ""
            score = title_match_score(product_name, title)
            if not passes_validation(score):
                continue
            return NormalisedOffer(
                source="ebay",
                price=p,
                currency=cur,
                product_title=title,
                product_url=href,
                seller_rating=None,
                review_count=None,
                in_stock=True,
                title_match_score=score,
                raw_price_text=f"{cur} {val}",
                metadata={"method": "api"},
            )
    except Exception:
        pass
    return None


async def scrape_ebay_html_playwright(search_query: str, product_name: str) -> NormalisedOffer | None:
    q = quote_plus(search_query[:200])
    url = f"https://www.ebay.com/sch/i.html?_nkw={q}&_sop=12"
    t0 = time.perf_counter()
    
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-US",
            {"width": 414, "height": 896},
            wait_selectors=[".s-item", ".s-item__wrapper", "[data-item-id]"],
            timeout=10000,
        )
    except Exception:
        html = None
    
    if not html or _is_ebay_blocked(html, url):
        return None
    
    latency_ms = (time.perf_counter() - t0) * 1000
    soup = BeautifulSoup(html, "lxml")
    items = soup.select(".s-item, .s-item__wrapper")[:15]
    for it in items:
        title_el = it.select_one(".s-item__title")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        if title.lower().startswith("shop on ebay") or "new listing" in title.lower():
            if it == items[0] and title.lower().startswith("shop on ebay"):
                continue
        
        price_el = it.select_one(".s-item__price, .s-item__detail span, .s-item__price span")
        link_el = it.select_one(".s-item__link")
        if not price_el or not link_el:
            continue
            
        raw = price_el.get_text(" ", strip=True)
        p, cur = strip_currency(raw)
        if p is None:
            m = re.search(r"([\d,]+\.?\d*)", raw)
            if m:
                p = float(m.group(1).replace(",", ""))
                cur = cur or "USD"
            else:
                continue
            
        href = link_el.get("href") or ""
        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue
            
        return NormalisedOffer(
            source="ebay",
            price=p,
            currency=cur or "USD",
            product_title=title,
            product_url=href,
            seller_rating=None,
            review_count=None,
            in_stock=True,
            title_match_score=score,
            raw_price_text=raw,
            metadata={"latency_ms": latency_ms, "method": "playwright"},
        )
    
    return None


async def scrape_ebay_html(search_query: str, product_name: str) -> NormalisedOffer | None:
    q = quote_plus(search_query[:200])
    url = f"https://www.ebay.com/sch/i.html?_nkw={q}&_sop=12"
    headers = get_browser_headers("generic")
    headers["Referer"] = "https://www.ebay.com/"
    
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers, http2=False) as client:
            r = await client.get(url)
            latency_ms = (time.perf_counter() - t0) * 1000
            
            if r.status_code in {403, 429}:
                return None
            if r.status_code != 200:
                return None
            
            if _is_ebay_blocked(r.text, str(r.url)):
                return None
                
            soup = BeautifulSoup(r.text, "lxml")
            items = soup.select(".s-item, .s-item__wrapper")[:15]
            for it in items:
                title_el = it.select_one(".s-item__title")
                if not title_el:
                    continue
                title = title_el.get_text(" ", strip=True)
                if title.lower().startswith("shop on ebay") or "new listing" in title.lower():
                    if it == items[0] and title.lower().startswith("shop on ebay"):
                        continue
                
                price_el = it.select_one(".s-item__price, .s-item__detail span, .s-item__price span")
                link_el = it.select_one(".s-item__link")
                if not price_el or not link_el:
                    continue
                    
                raw = price_el.get_text(" ", strip=True)
                p, cur = strip_currency(raw)
                if p is None:
                    m = re.search(r"([\d,]+\.?\d*)", raw)
                    if m:
                        p = float(m.group(1).replace(",", ""))
                        cur = cur or "USD"
                    else:
                        continue
                    
                href = link_el.get("href") or ""
                score = title_match_score(product_name, title)
                if not passes_validation(score):
                    continue
                    
                return NormalisedOffer(
                    source="ebay",
                    price=p,
                    currency=cur or "USD",
                    product_title=title,
                    product_url=href,
                    seller_rating=None,
                    review_count=None,
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


async def scrape_ebay(search_query: str, product_name: str, session_id: str) -> NormalisedOffer | None:
    # Try HTTP first (fastest path for most cases)
    offer = await scrape_ebay_html(search_query, product_name)
    if offer:
        return offer
    
    # Try API only if credentials are available
    cid = os.environ.get("EBAY_CLIENT_ID", "").strip()
    csec = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if cid and csec:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _ebay_oauth_token(client)
            if token:
                offer = await _browse_api_search(client, token, search_query[:200], product_name)
                if offer:
                    return offer
    
    # Final fallback: Playwright stealth mode
    return await scrape_ebay_html_playwright(search_query, product_name)

