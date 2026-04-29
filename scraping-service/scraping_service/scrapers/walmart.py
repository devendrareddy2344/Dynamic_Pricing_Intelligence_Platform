import asyncio
import json
import re
import time
import random
from urllib.parse import quote_plus, unquote

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers, get_mobile_headers
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth
from scraping_service.user_agents import random_ua, random_mobile_ua

TRUSTED_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


def _is_walmart_blocked(html: str, url: str) -> bool:
    """Detect Walmart bot-block challenges with improved sensitivity."""
    text = (html or "").lower()
    blocked_signals = [
        "robot or human",
        "verify you are human",
        "unusual traffic",
        "are you a robot",
        "captcha",
        "blocked",
        "access denied",
        "perimetrx",
        "px-captcha",
    ]
    # A valid Walmart search page is typically > 100KB. 
    # Challenge pages or simple redirects are usually < 30KB.
    is_too_small = len(html) < 20000 
    has_signal = any(signal in text for signal in blocked_signals)
    
    return has_signal or is_too_small or "/blocked" in str(url)


def _extract_walmart_price_text(card) -> str:
    """Extract price text from a Walmart card using multiple strategies (from 'last day' config)."""
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


def _parse_walmart_usd_price(raw: str) -> float | None:
    """Parse a USD price string (synchronized with 'last day' helper)."""
    if not raw:
        return None
    raw = raw.strip()
    p_val, _ = strip_currency(raw, "USD")
    if p_val is not None:
        return p_val if 0 < p_val < 15000 else None
    
    m = re.search(r"\$?\s?([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        price = float(m.group(1).replace(",", ""))
        return price if 0 < price < 15000 else None
    except ValueError:
        return None


async def _scrape_via_search_engine_fallback(
    product_name: str,
    t0: float,
) -> NormalisedOffer | None:
    """Stage 3 & 4: Search Engine (DDG & Bing) fallback for extreme blocking.
    Parses search results to find the best match and extract price.
    """
    targets = [
        # (Name, URL, Result Selector, Title Selector, Snippet Selector)
        ("ddg", f"https://html.duckduckgo.com/html/?q={quote_plus('site:walmart.com ' + product_name[:60] + ' price')}", "div.result", "a.result__a", "div.result__snippet"),
        ("bing", f"https://www.bing.com/search?q={quote_plus('site:walmart.com/ip ' + product_name[:60])}&form=QBLH", "li.b_algo", "h2 a", "div.b_caption p, div.b_snippet")
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }
    
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
        for name, url, res_sel, title_sel, snippet_sel in targets:
            try:
                if name != targets[0][0]: await asyncio.sleep(random.uniform(0.5, 1.5))
                res = await client.get(url)
                if res.status_code != 200: continue
                
                soup = BeautifulSoup(res.text, "lxml")
                results = soup.select(res_sel)
                if not results and name == "ddg": 
                    results = soup.select(".web-result")
                
                best_match = None
                max_score = -1.0

                for res_item in results[:8]:
                    title_el = res_item.select_one(title_sel)
                    snippet_el = res_item.select_one(snippet_sel)
                    
                    if not title_el: continue
                    title = title_el.get_text(strip=True)
                    href = title_el.get("href", "")
                    
                    if "walmart.com" not in href.lower(): continue
                    
                    snippet = snippet_el.get_text() if snippet_el else ""
                    combined_text = f"{title} {snippet}"
                    found_prices = re.findall(r"\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", combined_text)
                    
                    if not found_prices: continue
                    
                    prices = []
                    for p in found_prices:
                        try:
                            val = float(p.replace(",", ""))
                            if 5 < val < 15000: prices.append(val)
                        except: continue
                    
                    if not prices: continue
                    
                    is_electronics = any(kw in product_name.lower() for kw in ["s24", "iphone", "pixel", "laptop", "macbook", "tv", "console", "monitor"])
                    current_price = max(prices) if is_electronics else min(prices)
                    
                    score = title_match_score(product_name, title)
                    if score > max_score and passes_validation(score, threshold=0.4):
                        max_score = score
                        best_match = (title, current_price, href)
                
                if best_match:
                    title, price, href = best_match
                    return NormalisedOffer(
                        source="walmart",
                        price=price,
                        currency="USD",
                        product_title=title,
                        product_url=href,
                        in_stock=True,
                        title_match_score=max_score,
                        raw_price_text=f"~${price} ({name} snippet)",
                        metadata={"method": f"{name}_snippet", "latency_ms": (time.perf_counter() - t0) * 1000}
                    )
            except Exception:
                continue
    return None


async def scrape_walmart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Walmart US search via httpx (Stage 1) + Playwright (Stage 2) + Search Snippets (Stage 3/4)."""
    q_text = search_query if len(search_query) > 5 else product_name
    url = f"https://www.walmart.com/search?q={quote_plus(q_text[:120])}"
    t0 = time.perf_counter()

    # Stage 1: httpx mobile (Fastest)
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
                )
                for card in cards[:12]:
                    title_el = (
                        card.select_one("[data-automation-id='product-title']")
                        or card.select_one("span.lh-title, span.f-heading-3")
                        or card.select_one("a.b_a.b_g")
                    )
                    if not title_el: continue
                    title = title_el.get_text(" ", strip=True)
                    href = card.select_one("a").get("href") if card.select_one("a") else ""
                    if href.startswith("/"): href = "https://www.walmart.com" + href
                    raw = _extract_walmart_price_text(card)
                    p_val = _parse_walmart_usd_price(raw)
                    if p_val is None: continue
                    score = title_match_score(product_name, title)
                    if not passes_validation(score): continue
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

    # Stage 2: Playwright Stealth (Mobile Viewport fallback)
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-US",
            {"width": 414, "height": 896},
            wait_selectors=[
                "[data-item-id]", 
                "div[class*='search-result']", 
                "div[data-testid='item-stack']",
                "span.price-characteristic",
                "a[aria-label*='Dell']"
            ],
            timeout=40000,
        )
        if html and not _is_walmart_blocked(html, url):
            soup = BeautifulSoup(html, "lxml")
            latency_ms = (time.perf_counter() - t0) * 1000
            
            cards = (
                soup.select("[data-item-id]")
                or soup.select("div[class*='search-result']")
                or soup.select(".search-result-gridview-item")
                or soup.select("div[data-testid='item-stack']")
            )
            for card in cards[:12]:
                title_el = (
                    card.select_one("[data-automation-id='product-title']")
                    or card.select_one("span.lh-title, span.f-heading-3")
                    or card.select_one("a.b_a.b_g")
                )
                if not title_el: continue
                title = title_el.get_text(" ", strip=True)
                href = card.select_one("a").get("href") if card.select_one("a") else ""
                if href.startswith("/"): href = "https://www.walmart.com" + href
                raw = _extract_walmart_price_text(card)
                p_val = _parse_walmart_usd_price(raw)
                if p_val is None: continue
                score = title_match_score(product_name, title)
                if not passes_validation(score): continue
                return NormalisedOffer(
                    source="walmart",
                    price=p_val,
                    currency="USD",
                    product_title=title,
                    product_url=href or url,
                    in_stock=True,
                    title_match_score=score,
                    raw_price_text=raw,
                    metadata={"latency_ms": latency_ms, "method": "playwright"},
                )
    except Exception:
        pass

    # Stage 3/4: Ultimate Fallback (Search Engine Snippets)
    # Re-introduced hardening logic after PerimeterX blocks
    offer = await _scrape_via_search_engine_fallback(product_name, t0)
    if offer: return offer

    return None
