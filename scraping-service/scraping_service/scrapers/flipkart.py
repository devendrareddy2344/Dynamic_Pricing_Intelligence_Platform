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


async def scrape_flipkart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Flipkart India search via httpx + UA rotation (faster than Playwright)."""
    q = quote_plus(search_query[:120])
    url = f"https://www.flipkart.com/search?q={q}"
    t0 = time.perf_counter()
    
    # Try multiple attempts with different UAs and request fingerprints if blocked
    for attempt in range(3):  # Increased attempts
        if attempt < 2:
            headers = get_mobile_headers("flipkart")  # Use mobile first
            headers["User-Agent"] = random_mobile_ua()
        else:
            headers = get_browser_headers("flipkart")
            headers["User-Agent"] = random_ua()
        headers.setdefault("Referer", "https://www.google.com/")
        
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=False, headers=headers, http2=False) as client:
                r = await client.get(url)
                
                # Check for bot challenge text and block codes
                text_lower = r.text.lower()
                if any(x in text_lower for x in ["robot check", "suspicious activity", "blocked", "forbidden"]):
                    continue
                if r.status_code in {403, 429}:
                    continue
                if r.status_code != 200:
                    if r.status_code == 404:
                        return None
                    continue
                
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
                
                if not cards:
                    continue
                
                for card in cards[:12]:
                    title_el = (
                        card.select_one("a.s1Q9rs") or card.select_one("a.IRpwTe")
                        or card.select_one("h2 a") or card.select_one(".KzDlHZ a") or card.select_one("div._4rR01T")
                    )
                    price_el = (
                        card.select_one("div.Nx9Z0j") or card.select_one("div.Nx9bqj")
                        or card.select_one("span._30jeq3") or card.select_one("div._30jeq3")
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
        except asyncio.TimeoutError:
            continue
        except Exception:
            continue
    
    # Fallback: use Playwright if HTTP scraping is blocked by Flipkart
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_mobile_ua(),
            "en-IN",
            {"width": 414, "height": 896},
            wait_selectors=["div._4rR01T", "a.s1Q9rs", "a.IRpwTe", "div._1AtVbE", "div.yNoU89", "div._13oc-S"],
            timeout=10000,
        )
    except Exception:
        html = None

    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    seen_cards: set[tuple] = set()

    for title_text in soup.find_all(string=re.compile(re.escape(product_name), re.I)):
        raw_title = title_text.strip()
        if len(raw_title) > 80 or "buy products online" in raw_title.lower():
            continue

        current = title_text.parent
        card = None
        for _ in range(8):
            if current is None:
                break
            combined = " ".join(current.stripped_strings)
            if "₹" in combined:
                card = current
                break
            current = current.parent

        if not card:
            continue

        card_key = (card.name, tuple(card.get("class") or []), card.get("style"))
        if card_key in seen_cards:
            continue
        seen_cards.add(card_key)

        price_text = next((s.strip() for s in card.stripped_strings if "₹" in s), None)
        if not price_text:
            continue

        p, _ = strip_currency(price_text, "INR")
        if p is None:
            continue

        score = title_match_score(product_name, raw_title)
        if not passes_validation(score):
            continue

        href_el = card.find("a", href=True)
        href = href_el.get("href") if href_el else url
        if href.startswith("/"):
            href = "https://www.flipkart.com" + href

        return NormalisedOffer(
            source="flipkart",
            price=p,
            currency="INR",
            product_title=raw_title,
            product_url=href or url,
            in_stock=True,
            title_match_score=score,
            raw_price_text=price_text,
            metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "playwright"},
        )

    return None

