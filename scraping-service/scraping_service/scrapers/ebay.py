"""
ebay.py — eBay scraper with three-stage fallback strategy:

  1. HTTP scraping (fastest, works when eBay doesn't challenge)
  2. eBay Browse API (reliable, requires EBAY_CLIENT_ID + EBAY_CLIENT_SECRET)
  3. Playwright stealth (last resort for bot-challenge pages)
"""
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

_BLOCK_SIGNALS = [
    "captcha",
    "robot check",
    "access denied",
    "temporarily unavailable",
    "blocked",
    "unusual activity",
    "verify yourself",
]


def _is_ebay_blocked(html: str, url: str) -> bool:
    text = (html or "").lower()
    url_blocked = "block" in str(url).lower() or "captcha" in str(url).lower()
    return any(sig in text for sig in _BLOCK_SIGNALS) or url_blocked or len(text) < 1200


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
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        return r.json().get("access_token")
    except Exception:
        return None


async def _browse_api_search(
    client: httpx.AsyncClient, token: str, q: str, product_name: str, t0: float
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
            try:
                p = float(str(val).replace(",", ""))
            except ValueError:
                continue
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
                metadata={
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "method": "api",
                },
            )
    except Exception:
        pass
    return None


def _parse_ebay_items(
    soup: BeautifulSoup, product_name: str, t0: float, method: str
) -> NormalisedOffer | None:
    items = soup.select(".s-item__wrapper, .s-item")[:15]
    for it in items:
        title_el = it.select_one(".s-item__title")
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)
        # Skip eBay's own placeholder cards
        if "shop on ebay" in title.lower():
            continue

        price_el = it.select_one(
            ".s-item__price span.POSITIVE, "
            ".s-item__price, "
            ".s-item__detail .s-item__price"
        )
        link_el = it.select_one(".s-item__link")
        if not price_el or not link_el:
            continue

        raw = price_el.get_text(" ", strip=True)
        # Skip price ranges — prefer single prices for accuracy
        if "to" in raw.lower() or " - " in raw:
            # Use the lower bound
            parts = re.split(r"\s+to\s+|\s+-\s+", raw, flags=re.IGNORECASE)
            raw = parts[0].strip() if parts else raw

        p, cur = strip_currency(raw)
        if p is None:
            m = re.search(r"([\d,]+\.?\d*)", raw)
            if m:
                try:
                    p = float(m.group(1).replace(",", ""))
                    cur = cur or "USD"
                except ValueError:
                    continue
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
            metadata={
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                "method": method,
            },
        )
    return None


async def scrape_ebay(
    search_query: str, product_name: str, session_id: str
) -> NormalisedOffer | None:
    q = quote_plus(search_query[:200])
    # _sop=12 = "Best Match" sort
    url = f"https://www.ebay.com/sch/i.html?_nkw={q}&_sop=12&_ipg=24"
    t0 = time.perf_counter()

    # ── Stage 1: Plain HTTP (fastest) ───────────────────────────────────────
    headers = get_browser_headers("ebay")
    headers["Referer"] = "https://www.ebay.com/"
    try:
        async with httpx.AsyncClient(
            timeout=12.0, follow_redirects=True, headers=headers, http2=False
        ) as client:
            r = await client.get(url)
        if r.status_code == 200 and not _is_ebay_blocked(r.text, str(r.url)):
            soup = BeautifulSoup(r.text, "lxml")
            offer = _parse_ebay_items(soup, product_name, t0, "httpx")
            if offer:
                return offer
    except Exception:
        pass

    # ── Stage 2: Browse API (if credentials are configured) ─────────────────
    cid = os.environ.get("EBAY_CLIENT_ID", "").strip()
    csec = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
    if cid and csec:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                token = await _ebay_oauth_token(client)
                if token:
                    offer = await _browse_api_search(
                        client, token, search_query[:200], product_name, t0
                    )
                    if offer:
                        return offer
        except Exception:
            pass

    # ── Stage 3: Playwright stealth (anti-bot fallback) ─────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-US",
            {"width": 414, "height": 896},
            wait_selectors=[".s-item", ".s-item__wrapper", "[data-view='mi:1686|iid:1']"],
            timeout=40000,
        )
    except Exception:
        html = None

    if html and not _is_ebay_blocked(html, url):
        soup = BeautifulSoup(html, "lxml")
        return _parse_ebay_items(soup, product_name, t0, "playwright")

    return None
