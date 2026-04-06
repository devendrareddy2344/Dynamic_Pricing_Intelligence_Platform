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
from scraping_service.scrapers.headers import get_browser_headers
from scraping_service.user_agents import random_ua




async def scrape_amazon(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Amazon search with robust UA rotation and anti-bot logic."""
    q = quote_plus(search_query[:200])
    
    # Detect region
    region = os.environ.get("REGION", "us").lower()
    tld = "in" if region == "in" else "com"
    base_url = f"https://www.amazon.{tld}"
    url = f"{base_url}/s?k={q}"
    
    t0 = time.perf_counter()
    
    # Try multiple attempts with different UAs if blocked
    for attempt in range(2):
        headers = get_browser_headers("amazon")
        headers["User-Agent"] = random_ua() # Override with broader selection
        
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers, http2=True) as client:
                r = await client.get(url)
                
                # Check for bot challenge
                text_lower = r.text.lower()
                if any(x in text_lower for x in ["captcha", "robot check", "sorry! something went wrong", "api-services-support@amazon.com"]):
                    if attempt == 0: continue # Try again with new UA
                    return None

                if r.status_code != 200:
                    if r.status_code == 404: return None
                    continue

                latency_ms = (time.perf_counter() - t0) * 1000

                soup = BeautifulSoup(r.text, "lxml")
                items = (
                    soup.select("[data-component-type='s-search-result']") 
                    or soup.select(".s-result-item")
                    or soup.select("div[data-asin]")
                )
                
                for it in items[:15]:
                    title_el = (
                        it.select_one("h2 a span") 
                        or it.select_one("h2 span")
                        or it.select_one(".a-size-medium")
                        or it.select_one(".a-size-base-plus")
                        or it.select_one(".a-text-normal")
                    )
                    link_el = it.select_one("h2 a") or it.select_one("a.a-link-normal") or it.select_one(".a-link-normal")
                    
                    if not title_el or not link_el:
                        continue
                        
                    title = title_el.get_text(" ", strip=True)
                    href = link_el.get("href") or ""
                    if href.startswith("/"):
                        href = base_url + href

                    price_whole = it.select_one(".a-price-whole")
                    price_frac = it.select_one(".a-price-fraction")
                    off = it.select_one(".a-offscreen")
                    
                    raw = ""
                    if off:
                        raw = off.get_text(strip=True)
                    elif price_whole:
                        raw = price_whole.get_text(strip=True).replace(".", "")
                        if price_frac:
                            raw += "." + price_frac.get_text(strip=True)
                    
                    if not raw:
                        # Try generic price extraction
                        price_txt = it.get_text(" ", strip=True)
                        m = re.search(r"[\$₹]\s?([\d,]+\.?\d*)", price_txt)
                        if m: raw = m.group(0)
                        else: continue
                        
                    p, detected_cur = strip_currency(raw, "INR" if tld == "in" else "USD")
                    if p is None:
                        continue
                        
                    score = title_match_score(product_name, title)
                    if not passes_validation(score):
                        continue

                    return NormalisedOffer(
                        source="amazon",
                        price=p,
                        currency=detected_cur,
                        product_title=title,
                        product_url=href,
                        in_stock=True,
                        title_match_score=score,
                        raw_price_text=raw,
                        metadata={"latency_ms": latency_ms, "method": "html"},
                    )
        except Exception:
            if attempt == 0: continue
            
    return None



