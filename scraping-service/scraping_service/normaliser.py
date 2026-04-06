import os
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

_PRICE_RANGE = re.compile(
    r"[\$€£₹]?\s*([\d,]+\.?\d*)\s*[–\-—]\s*[\$€£₹]?\s*([\d,]+\.?\d*)",
    re.I,
)
_SINGLE = re.compile(r"[\$€£₹]?\s*([\d,]+\.?\d*)")


@dataclass
class NormalisedOffer:
    source: str
    price: float
    currency: str
    product_title: str
    product_url: str
    seller_rating: float | None = None
    review_count: int | None = None
    in_stock: bool | None = None
    title_match_score: float = 0.0
    raw_price_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def strip_currency(text: str, default_currency: str | None = None) -> tuple[float | None, str]:
    """Extract first price from text and detect currency symbol."""
    t = text.strip().replace("\xa0", " ").replace("\u20b9", "₹").replace("\u2022", "").replace(",", "")
    
    # Detect currency
    cur = default_currency or os.environ.get("CURRENCY", "USD")
    if "₹" in t or "Rs" in t or "INR" in t:
        cur = "INR"
    elif "$" in t or "USD" in t:
        cur = "USD"
    elif "€" in t or "EUR" in t:
        cur = "EUR"
    elif "£" in t or "GBP" in t:
        cur = "GBP"
        
    m = _PRICE_RANGE.search(t)
    if m:
        try:
            a = float(m.group(1))
            b = float(m.group(2))
            price = min(a, b)
            # High sanity ceiling: ₹5 Lakhs for INR, $10k for USD/others
            ceiling = 500000 if cur == "INR" else 10000
            return (price if price < ceiling else None), cur
        except ValueError:
            pass
            
    m2 = _SINGLE.search(t)
    if m2:
        try:
            price = float(m2.group(1))
            ceiling = 500000 if cur == "INR" else 10000
            return (price if price < ceiling else None), cur
        except ValueError:
            pass

            
    return None, cur



def isolation_flag_prices(prices: list[float], contamination: float = 0.15) -> list[bool]:
    if len(prices) < 3:
        return [False] * len(prices)
    X = np.array(prices).reshape(-1, 1)
    iso = IsolationForest(contamination=min(contamination, 0.5), random_state=42)
    iso.fit(X)
    pred = iso.predict(X)
    return [bool(p == -1) for p in pred]
