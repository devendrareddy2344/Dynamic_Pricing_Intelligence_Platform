import asyncio
import json
import re
import time
from urllib.parse import quote_plus, unquote

import httpx
from bs4 import BeautifulSoup

from scraping_service.normaliser import NormalisedOffer, strip_currency
from scraping_service.scrapers.match import passes_validation, title_match_score
from scraping_service.scrapers.headers import get_browser_headers, get_mobile_headers
from scraping_service.scrapers.playwright_utils import fetch_page_html_with_stealth
from scraping_service.user_agents import random_ua, random_mobile_ua


def _is_walmart_blocked(html: str, url: str, playwright: bool = False) -> bool:
    text = (html or "").lower()
    blocked_signals = [
        "robot or human",
        "verify you are human",
        "unusual traffic",
        "are you a robot",
        "captcha",
        "blocked",
        "access denied",
        "bot traffic",
    ]
    url_blocked = "/blocked" in str(url).lower()
    # Playwright renders a full browser session; real Walmart pages are 300KB+.
    # A Playwright result under 50 KB is almost certainly a bot-block challenge page.
    # For plain httpx we use the same threshold to filter challenge pages early.
    min_size = 50_000
    return any(signal in text for signal in blocked_signals) or url_blocked or len(text) < min_size


def _parse_walmart_price(card) -> str:
    """Extract price text from a Walmart card using multiple strategies."""
    # Strategy 1: Structured whole+mantissa spans
    whole = card.select_one("span.price-characteristic")
    if whole:
        text = whole.get_text(strip=True)
        mantissa = card.select_one("span.price-mantissa")
        if mantissa:
            text = f"{text}.{mantissa.get_text(strip=True)}"
        return text

    # Strategy 2: price-group wrapper span
    group = card.select_one("span.price-group")
    if group:
        return group.get_text(" ", strip=True)

    # Strategy 3: data-automation attribute selectors (2024+ layout)
    for sel in [
        "[data-automation-id='product-price']",
        "span[data-automation-id='product-price']",
        "div[data-testid='price-wrap'] span",
        ".f2.b.black.lh-copy",
        "span.inline-flex.flex-column",
    ]:
        el = card.select_one(sel)
        if el:
            return el.get_text(" ", strip=True)

    # Strategy 4: Fallback generic text search
    txt = card.get_text(" ", strip=True)
    m = re.search(r"\$\s?([\d,]+\.?\d*)", txt)
    if m:
        return m.group(0)

    return ""


def _parse_price_value(raw: str) -> float | None:
    """Parse a USD price string into a float."""
    if not raw:
        return None
    p_val, _ = strip_currency(raw.strip(), "USD")
    if p_val is not None:
        return p_val if 0 < p_val < 10000 else None
    m = re.search(r"\$?\s?([\d,]+\.?\d*)", raw)
    if not m:
        return None
    try:
        price = float(m.group(1).replace(",", ""))
        return price if 0 < price < 10000 else None
    except ValueError:
        return None


def _parse_walmart_cards(
    soup: BeautifulSoup, product_name: str, url: str, latency_ms: float, method: str
) -> NormalisedOffer | None:
    """Shared card parsing logic for both Playwright and httpx paths."""
    cards = (
        soup.select("[data-item-id]")
        or soup.select("div[data-testid='item-stack']")
        or soup.select("div[class*='search-result-gridview-item']")
        or soup.select("div[class*='search-result']")
        # 2024 layout
        or soup.select("div[data-testid='list-view']")
        or soup.select("section[data-testid*='product']")
    )

    for card in cards[:12]:
        title_el = (
            card.select_one("[data-automation-id='product-title']")
            or card.select_one("span[data-automation-id='product-title']")
            or card.select_one("span.lh-title")
            or card.select_one("span.f-heading-3")
            # 2024 layout
            or card.select_one("a[link-identifier]")
            or card.select_one("span[class*='lh-copy'][class*='normal']")
            or card.select_one("a[href]")
        )
        if not title_el:
            continue

        title = title_el.get_text(" ", strip=True)
        if len(title) < 5:
            continue

        # Resolve product link safely
        first_a = card.select_one("a[href]")
        href = first_a.get("href", "") if first_a else ""
        if href.startswith("/"):
            href = "https://www.walmart.com" + href

        raw = _parse_walmart_price(card)
        p_val = _parse_price_value(raw)
        if p_val is None:
            continue

        score = title_match_score(product_name, title)
        if not passes_validation(score):
            continue

        return NormalisedOffer(
            source="walmart",
            price=p_val,
            currency="USD",
            product_title=title,
            product_url=href or url,
            in_stock=True,
            title_match_score=score,
            raw_price_text=raw,
            metadata={"latency_ms": round(latency_ms, 2), "method": method},
        )
    return None


# In-memory cache: product_name -> walmart item_id
# Avoids hitting DDG/Bing repeatedly for the same product across sessions
_WALMART_ITEM_CACHE: dict[str, str] = {}


async def _find_walmart_item_id(product_name: str) -> str | None:
    """Search DDG then Bing HTML to find a walmart.com/ip item ID.
    Returns the numeric item ID string, or None if not found.
    """
    cache_key = product_name.lower().strip()
    if cache_key in _WALMART_ITEM_CACHE:
        return _WALMART_ITEM_CACHE[cache_key]

    _WALMART_ID_RE = re.compile(r"walmart\.com/ip/[^/]+/(\d+)")

    async def _search(search_url: str, link_sel: str, param: str) -> str | None:
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as c:
                r = await c.get(search_url, headers=h)
            # Accept only responses large enough to be real results (not bot challenges)
            if r.status_code not in (200, 202) or len(r.text) < 20_000:
                return None
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.select(link_sel):
                href = a.get("href", "")
                if param:
                    m_p = re.search(rf"{re.escape(param)}=([^&]+)", href)
                    if m_p:
                        href = unquote(m_p.group(1))
                m_id = _WALMART_ID_RE.search(href)
                if m_id:
                    return m_id.group(1)
        except Exception:
            pass
        return None

    q_enc = quote_plus(f"site:walmart.com {product_name[:60]}")

    # 1️⃣  DuckDuckGo HTML
    item_id = await _search(
        f"https://html.duckduckgo.com/html/?q={q_enc}",
        ".result a[href]",
        "uddg",
    )

    # 2️⃣  Bing HTML fallback (different rate-limit bucket)
    if not item_id:
        q_bing = quote_plus(f"site:walmart.com/ip {product_name[:60]}")
        item_id = await _search(
            f"https://www.bing.com/search?q={q_bing}&cc=US&setlang=en-US&form=QBLH",
            "a[href]",
            "",  # Bing uses unencoded real URLs in href
        )

    if item_id:
        _WALMART_ITEM_CACHE[cache_key] = item_id

    return item_id


async def _scrape_via_ddg_fallback(
    product_name: str,
    session_id: str,
    t0: float,
) -> NormalisedOffer | None:
    """Stage 3: Use DuckDuckGo HTML search to find a walmart.com/ip/ item ID,
    then fetch the short-form product page directly (bypasses PerimeterX).

    """
    try:
        item_id = await _find_walmart_item_id(product_name)
        if not item_id:
            return None

        # Use short /ip/item/<id> form — not gated by PerimeterX
        item_url = f"https://www.walmart.com/ip/item/{item_id}"
        item_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            ir = await client.get(item_url, headers=item_headers)
        if ir.status_code != 200 or len(ir.text) < 50_000:
            return None

        html = ir.text

        # Strategy 1: JSON-LD structured data
        soup2 = BeautifulSoup(html, "lxml")
        for script in soup2.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    price_raw = None
                    offer_block = entry.get("offers") or entry.get("Offers")
                    if isinstance(offer_block, dict):
                        price_raw = offer_block.get("price") or offer_block.get("lowPrice")
                    elif isinstance(offer_block, list) and offer_block:
                        price_raw = offer_block[0].get("price")
                    if price_raw is not None:
                        title = entry.get("name", product_name)
                        p_val = float(str(price_raw).replace(",", ""))
                        if p_val > 0:
                            score = title_match_score(product_name, title)
                            if passes_validation(score):
                                return NormalisedOffer(
                                    source="walmart",
                                    price=p_val,
                                    currency="USD",
                                    product_title=title,
                                    product_url=item_url,
                                    in_stock=True,
                                    title_match_score=score,
                                    raw_price_text=f"${p_val}",
                                    metadata={
                                        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                                        "method": "ddg_item_page",
                                    },
                                )
            except Exception:
                continue

        # Strategy 2: Multiple regex patterns over embedded Next.js JSON blobs
        title_m = re.search(r'"productName":"([^"]+)"', html)
        title = title_m.group(1) if title_m else product_name
        price_patterns = [
            r'"priceInfo":\{"[^}]*"itemPrice":"(\$?[\d.]+)"',
            r'"price":\s*([\d.]+)',
            r'"currentPrice":([\d.]+)',
            r'"salePrice":([\d.]+)',
        ]
        for pat in price_patterns:
            m_p = re.search(pat, html)
            if m_p:
                try:
                    p_val = float(m_p.group(1).replace("$", "").replace(",", ""))
                    if 1 < p_val < 10000:
                        score = title_match_score(product_name, title)
                        if passes_validation(score):
                            return NormalisedOffer(
                                source="walmart",
                                price=p_val,
                                currency="USD",
                                product_title=title,
                                product_url=item_url,
                                in_stock=True,
                                title_match_score=score,
                                raw_price_text=f"${p_val}",
                                metadata={
                                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                                    "method": "ddg_item_page_regex",
                                },
                            )
                except Exception:
                    continue

        return None
    except Exception:
        return None


async def scrape_walmart(
    search_query: str,
    product_name: str,
    session_id: str,
) -> NormalisedOffer | None:
    """Walmart US search via Playwright-stealth (primary) + httpx (fallback).

    Walmart uses PerimeterX bot protection; Playwright stealth is essential.
    Stage 3 falls back to DuckDuckGo → direct item page when search is blocked.
    """
    q = quote_plus(product_name[:80] if len(product_name) > 5 else search_query[:80])
    url = f"https://www.walmart.com/search?q={q}"
    t0 = time.perf_counter()

    # ── Stage 1: httpx mobile bypass (faster than Playwright) ────────────────
    headers = get_mobile_headers("walmart")
    headers["User-Agent"] = random_mobile_ua()
    headers.setdefault("Referer", "https://www.google.com/")

    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            trust_env=False,
            headers=headers,
            http2=False,
        ) as client:
            r = await client.get(url)

        if r.status_code == 200 and not _is_walmart_blocked(r.text, str(r.url)):
            latency_ms = (time.perf_counter() - t0) * 1000
            soup = BeautifulSoup(r.text, "lxml")
            offer = _parse_walmart_cards(soup, product_name, url, latency_ms, "httpx")
            if offer:
                return offer
    except Exception:
        pass

    # ── Stage 2: Playwright stealth (fallback) ───────────────────────────────
    try:
        html = await fetch_page_html_with_stealth(
            url,
            random_ua(),
            "en-US",
            {"width": 1280, "height": 800},
            wait_selectors=[
                "[data-item-id]",
                "div[data-testid='item-stack']",
                "div[data-testid='list-view']",
                "[data-automation-id='product-title']",
                "span.lh-title",
            ],
            timeout=45000,
            proxy_url=None,
        )
        if html and not _is_walmart_blocked(html, url, playwright=True):
            soup = BeautifulSoup(html, "lxml")
            offer = _parse_walmart_cards(
                soup, product_name, url,
                (time.perf_counter() - t0) * 1000, "playwright"
            )
            if offer:
                return offer
            else:
                with open("failed_walmart.html", "w", encoding="utf-8") as f:
                    f.write(html)
        elif html:
            with open("blocked_walmart.html", "w", encoding="utf-8") as f:
                f.write(html)
    except Exception:
        pass

    # ── Stage 3: DuckDuckGo → direct item page (bypasses PerimeterX on search)
    offer = await _scrape_via_ddg_fallback(product_name, session_id, t0)
    if offer:
        return offer

    return None
