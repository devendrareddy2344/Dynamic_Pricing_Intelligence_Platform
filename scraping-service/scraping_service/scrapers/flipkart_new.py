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
from scraping_service.user_agents import random_ua, random_mobile_ua


async def scrape_flipkart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Flipkart India search via httpx + Playwright fallback."""
    q = quote_plus(search_query[:120])
    url = f"https://www.flipkart.com/search?q={q}"
    t0 = time.perf_counter()
    
    # Try httpx with mobile headers first
    headers = get_mobile_headers("flipkart")
    headers["User-Agent"] = random_mobile_ua()
    headers.setdefault("Referer", "https://www.google.com/")
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
            r = await client.get(url)
            
            if r.status_code == 200 and not any(x in r.text.lower() for x in ["captcha", "robot check", "suspicious activity", "blocked", "forbidden"]):
                latency_ms = (time.perf_counter() - t0) * 1000
                
                soup = BeautifulSoup(r.text, "lxml")
                cards = (
                    soup.select("div[data-id]")
                    or soup.select("div._1AtVbE")
                    or soup.select("div.yNoU89")
                    or soup.select("div._13oc-S")
                    or soup.select("div._2kHMtA")
                    or soup.select("a._1fQZEK")
                )
                
                for card in cards[:12]:
                    title_el = (
                        card.select_one("a.s1Q9rs") or card.select_one("a.IRpwTe")
                        or card.select_one("h2 a") or card.select_one(".KzDlHZ a") or card.select_one("div._4rR01T")
                    )
                    price_el = (
                        card.select_one("div.Nx9Z0j") or card.select_one("div.Nx9bqj")
                        or card.select_one("span._30jeq3") or card.select_one("div._30jeq3")
                        or card.select_one("div._25b18c")
                    )
                    
                    if not title_el or not price_el:
                        continue
                    
                    title = title_el.get_text(" ", strip=True)
                    href = title_el.get("href") or ""
                    if href.startswith("/"):
                        href = "https://www.flipkart.com" + href
                    
                    raw = price_el.get_text(strip=True)
                    p, _ = strip_currency(raw, "INR")
                    if p is None:
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
                        metadata={"latency_ms": latency_ms, "method": "httpx"},
                    )
    except Exception:
        pass
    
    # Fallback: use Playwright if HTTP scraping is blocked by Flipkart
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(
                user_agent=random_mobile_ua(),
                locale="en-IN",
                viewport={"width": 414, "height": 896},
            )
            page = await context.new_page()
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            html = await page.content()
            await page.close()
            await context.close()
            await browser.close()

            soup = BeautifulSoup(html, "lxml")
            cards = (
                soup.select("div[data-id]")
                or soup.select("div._1AtVbE")
                or soup.select("div.yNoU89")
                or soup.select("div._13oc-S")
                or soup.select("div._2kHMtA")
                or soup.select("a._1fQZEK")
            )
            for card in cards[:12]:
                title_el = (
                    card.select_one("a.s1Q9rs") or card.select_one("a.IRpwTe")
                    or card.select_one("h2 a") or card.select_one(".KzDlHZ a") or card.select_one("div._4rR01T")
                )
                price_el = (
                    card.select_one("div.Nx9Z0j") or card.select_one("div.Nx9bqj")
                    or card.select_one("span._30jeq3") or card.select_one("div._30jeq3")
                    or card.select_one("div._25b18c")
                )
                if not title_el or not price_el:
                    continue
                title = title_el.get_text(" ", strip=True)
                href = title_el.get("href") or ""
                if href.startswith("/"):
                    href = "https://www.flipkart.com" + href
                raw = price_el.get_text(strip=True)
                p, _ = strip_currency(raw, "INR")
                if p is None:
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
                    metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "playwright"},
                )
    except Exception:
        return None

    return None