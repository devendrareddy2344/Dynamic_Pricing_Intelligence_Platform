import os
import re
import time
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers


async def scrape_tatacliq(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Tata CLiQ India search via their internal API + HTML fallback."""
    # Use product_name for Tatacliq to avoid category redirect bug (e.g. "monitor" -> "Health Monitor")
    q = quote_plus(product_name[:120])
    t0 = time.perf_counter()

    headers = get_browser_headers("tatacliq")
    
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            # ── API approach first ──────────────────────────────────────────
            api_url = (
                f"https://search.tatacliq.com/tatacliq/search"
                f"?q={q}&isPLP=true&searchCategory=all"
                f"&pageNo=1&pageSize=10&src=plp"
            )
            try:
                r = await client.get(api_url)
                if r.status_code == 200:
                    data = r.json()
                    products = (
                        data.get("searchData", {}).get("clpPromoDataList", [])
                        or data.get("products", [])
                        or data.get("results", [])
                        or []
                    )
                    for prod in products[:10]:
                        title = (
                            prod.get("productName", "")
                            or prod.get("name", "")
                            or prod.get("title", "")
                        )
                        price_val = (
                            prod.get("salesPrice")
                            or prod.get("price")
                            or prod.get("mrp")
                        )
                        if not title or price_val is None:
                            continue
                        slug = prod.get("productUrl", "") or prod.get("url", "")
                        href = f"https://www.tatacliq.com{slug}" if slug.startswith("/") else slug
                        score = title_match_score(product_name, title)
                        if not passes_validation(score):
                            continue
                        return NormalisedOffer(
                            source="tatacliq",
                            price=float(price_val),
                            currency="INR",
                            product_title=title,
                            product_url=href or "https://www.tatacliq.com",
                            seller_rating=prod.get("averageRating"),
                            review_count=prod.get("totalReviews"),
                            in_stock=prod.get("inStock", True),
                            title_match_score=score,
                            raw_price_text=f"₹{price_val}",
                            metadata={"latency_ms": (time.perf_counter() - t0) * 1000, "method": "api"},
                        )
            except Exception:
                pass

            # ── HTML fallback ──────────────────────────────────────────────
            html_url = f"https://www.tatacliq.com/search/?text={q}"
            r2 = await client.get(html_url)
            latency_ms = (time.perf_counter() - t0) * 1000
            if r2.status_code != 200:
                return None
            
            soup = BeautifulSoup(r2.text, "lxml")
            
            # Use selectors identified by inspection
            cards = (
                soup.select("a.ProductModule__base") 
                or soup.select("div.ProductModule__buy-section")
                or soup.select("div[class*='ProductModule']")
                or soup.select("li[class*='product']")
            )
            for card in cards[:10]:
                title_el = (
                    card.select_one("[class*='productName']") 
                    or card.select_one("[class*='title']") 
                    or card.select_one("[class*='name']")
                    or card.select_one("h2")
                    or card.select_one("h3")
                )
                price_el = (
                    card.select_one("[class*='productPrice']") 
                    or card.select_one("[class*='price']") 
                    or card.select_one("[class*='Price']")
                )
                
                if not title_el or not price_el:
                    continue
                
                title = title_el.get_text(" ", strip=True)
                raw = price_el.get_text(strip=True)
                
                href = card.get("href") or ""
                if not href:
                    link_el = card.select_one("a[href]")
                    href = link_el.get("href", "") if link_el else ""
                
                if href and href.startswith("/"):
                    href = "https://www.tatacliq.com" + href
                
                p, _ = strip_currency(raw, "INR")
                if p is None:
                    continue
                
                score = title_match_score(product_name, title)
                if not passes_validation(score):
                    continue
                
                return NormalisedOffer(
                    source="tatacliq",
                    price=p,
                    currency="INR",
                    product_title=title,
                    product_url=href or html_url,
                    seller_rating=None,
                    review_count=None,
                    in_stock=True,
                    title_match_score=score,
                    raw_price_text=raw,
                    metadata={"latency_ms": latency_ms, "method": "html"},
                )
    except Exception:
        pass
    return None
