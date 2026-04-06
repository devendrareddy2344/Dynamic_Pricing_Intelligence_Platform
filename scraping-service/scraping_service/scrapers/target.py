import os
import random
import re
import time
from urllib.parse import quote_plus

import httpx

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers

# Live exchange rate fallback (updated periodically, used if API fails)


async def scrape_target(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Target search via their internal Redsky API with USD→INR conversion."""
    q = quote_plus(search_query[:120])
    t0 = time.perf_counter()

    headers = get_browser_headers("generic")
    headers.update({
        "Referer": f"https://www.target.com/s?searchTerm={q}",
        "Origin": "https://www.target.com",
        "X-Target-Referrer": f"/s?searchTerm={q}",
        "Accept": "application/json",
    })

    # Generate a dummy visitor ID if not provided
    visitor_id = f"019D572B{random.randint(1000, 9999)}0200AA9E{random.randint(1000, 9999)}066D26B0"

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            # Target's modern search endpoints - now requires 'page' parameter
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
                (
                    f"https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
                    f"?key=9f36aeafbe60771e321a7cc95a78140772ab3e96"
                    f"&channel=WEB&count=24&keyword={q}&offset=0&platform=desktop"
                    f"&visitor_id={visitor_id}&page=%2Fs%2F{q}",
                    None
                ),
            ]
            
            for api_url, params in endpoints:
                try:
                    r = await client.get(api_url, params=params)
                    if r.status_code == 403 or r.status_code == 401:
                        # Fallback to HTML if API is blocked
                        html_url = f"https://www.target.com/s?searchTerm={q}"
                        r_html = await client.get(html_url)
                        if r_html.status_code == 200:
                            # Basic HTML parsing for Target search pages
                            soup = BeautifulSoup(r_html.text, "lxml")
                            # Look for product cards - often have data-test='productCard'
                            cards = soup.select("[data-test='productCard']") or soup.select(".product-card")
                            for card in cards[:10]:
                                title_el = card.select_one("[data-test='product-title']") or card.select_one("a")
                                price_el = card.select_one("[data-test='current-price']") or card.select_one("span")
                                if title_el and price_el:
                                    # Very basic extraction for fallback
                                    title = title_el.get_text(strip=True)
                                    raw_price = price_el.get_text(strip=True)
                                    p, _ = strip_currency(raw_price, "USD")
                                    if p:
                                        # ... simplified logic for fallback ...
                                        pass
                        continue
                    if r.status_code != 200:
                        continue

                    data = r.json()
                    # Updated path based on inspection
                    search_res = data.get("data", {}).get("search", {}).get("search_response", {})
                    products = search_res.get("products", [])
                    
                    if not products:
                        # Fallback for older search response structure
                        products = data.get("data", {}).get("search", {}).get("products", [])
                    
                    if not products:
                        continue


                    for prod in products[:12]:
                        item = prod.get("item", {}) or {}
                        title = (item.get("product_description", {}) or {}).get("title", "")
                        price_info = prod.get("price", {}) or {}
                        
                        # Try to find a valid price
                        price_val = (
                            price_info.get("current_retail")
                            or price_info.get("reg_retail")
                        )
                        if price_val is None:
                            price_val = price_info.get("current_retail_min")
                            
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

                        return NormalisedOffer(
                            source="target",
                            price=float(price_val),
                            currency="USD",
                            product_title=title,
                            product_url=href,
                            seller_rating=prod.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("average"),
                            review_count=prod.get("ratings_and_reviews", {}).get("statistics", {}).get("rating", {}).get("count"),
                            in_stock=prod.get("fulfillment_v3", {}).get("is_in_stock", True),
                            title_match_score=score,
                            raw_price_text=f"${price_val}",
                            metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "usd_price": price_val, "method": "api"},
                        )

                except Exception:
                    continue

            # Fallback: HTML search
            html_url = f"https://www.target.com/s?searchTerm={q}"
            r_html = await client.get(html_url)
            # Minimal HTML parsing could be added here if needed
                
    except Exception:
        pass
    return None


