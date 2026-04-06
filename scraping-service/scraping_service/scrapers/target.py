import os
import random
import re
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers


async def scrape_target(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Target search via their internal Redsky API + HTML fallback."""
    q = quote_plus(search_query[:120])
    t0 = time.perf_counter()

    headers = get_browser_headers("target")
    headers.update({
        "Referer": f"https://www.target.com/s?searchTerm={q}",
        "Origin": "https://www.target.com",
        "X-Target-Referrer": f"/s?searchTerm={q}",
        "Accept": "application/json",
    })

    visitor_id = f"019D572B{random.randint(1000, 9999)}0200AA9E{random.randint(1000, 9999)}066D26BA"

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            endpoints = [
                (
                    "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2",
                    {
                        "key": "9f36aeafbe60771e321a7cc95a78140772ab3e96",
                        "channel": "WEB",
                        "count": "24",
                        "default_purchasability_filter": "true",
                        "include_sponsored": "true",
                        "keyword": search_query,
                        "offset": "0",
                        "platform": "desktop",
                        "visitor_id": visitor_id,
                        "zip": "10001",
                        "page": f"/s/{search_query}",
                    }
                ),
            ]
            
            for api_url, params in endpoints:
                try:
                    r = await client.get(api_url, params=params)
                    if r.status_code in {401, 403, 429}:
                        continue
                    if r.status_code != 200:
                        continue

                    data = r.json()
                    search_res = data.get("data", {}).get("search", {}).get("search_response", {})
                    products = search_res.get("products", [])
                    
                    if not products:
                        products = data.get("data", {}).get("search", {}).get("products", [])
                    
                    for prod in products[:12]:
                        item = prod.get("item", {}) or {}
                        title = (item.get("product_description", {}) or {}).get("title", "")
                        price_info = prod.get("price", {}) or {}
                        
                        price_val = (
                            price_info.get("current_retail")
                            or price_info.get("reg_retail")
                            or price_info.get("current_retail_min")
                        )
                            
                        if price_val is None:
                            fmt = price_info.get("formatted_current_price", "")
                            m = re.search(r"[\d,]+\.?\d*", fmt)
                            price_val = float(m.group(0).replace(",", "")) if m else None
                        
                        if not title or price_val is None:
                            continue
                            
                        tcin = item.get("tcin", "")
                        href = f"https://www.target.com/p/-/A-{tcin}" if tcin else "https://www.target.com"
                        
                        score = title_match_score(product_name, title)
                        if not passes_validation(score):
                            continue

                        stats = prod.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {})
                        
                        return NormalisedOffer(
                            source="target",
                            price=float(price_val),
                            currency="USD",
                            product_title=title,
                            product_url=href,
                            seller_rating=stats.get("average"),
                            review_count=stats.get("count"),
                            in_stock=prod.get("fulfillment_v3", {}).get("is_in_stock", True),
                            title_match_score=score,
                            raw_price_text=f"${price_val}",
                            metadata={"latency_ms": round((time.perf_counter() - t0) * 1000, 2), "method": "api"},
                        )

                except Exception:
                    continue

            # HTML Fallback
            html_url = f"https://www.target.com/s?searchTerm={q}"
            fallback_headers = get_browser_headers("target")
            fallback_headers.setdefault("Referer", "https://www.google.com/")
            r_html = await client.get(html_url, headers=fallback_headers)
            
            if r_html.status_code == 200:
                soup = BeautifulSoup(r_html.text, "lxml")
                cards = soup.select("[data-test='productCard']") or soup.select(".product-card")
                for card in cards[:12]:
                    title_el = card.select_one("[data-test='product-title']") or card.select_one("a[aria-label]")
                    price_el = card.select_one("[data-test='current-price']") or card.select_one("span")
                    if not title_el or not price_el:
                        continue
                        
                    title = title_el.get_text(" ", strip=True)
                    raw_price = price_el.get_text(strip=True)
                    p, _ = strip_currency(raw_price, "USD")
                    
                    if p is None or p <= 0:
                        continue
                        
                    first_a = card.select_one("a[href]")
                    href = first_a.get("href", "") if first_a else ""
                    if href and href.startswith("/"):
                        href = "https://www.target.com" + href
                        
                    score = title_match_score(product_name, title)
                    if not passes_validation(score):
                        continue
                        
                    return NormalisedOffer(
                        source="target",
                        price=p,
                        currency="USD",
                        product_title=title,
                        product_url=href or html_url,
                        title_match_score=score,
                        raw_price_text=raw_price,
                        metadata={"latency_ms": round((time.perf_counter() - t0) * 1000, 2), "method": "httpx"},
                    )
                
    except Exception:
        pass
    return None
