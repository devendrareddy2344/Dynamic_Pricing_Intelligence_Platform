"""Demand signal proxy from review counts, ratings, badges (0–100)."""


def demand_score(
    review_count: int | None,
    seller_rating: float | None,
    has_bestseller_badge: bool = False,
) -> float:
    rc = float(review_count or 0)
    rating = float(seller_rating or 3.5)
    # Log-scaled review volume + rating curve
    import math

    vol = min(100.0, 25.0 * math.log1p(rc / 50.0))
    qual = max(0.0, min(50.0, (rating - 3.0) * 12.5))
    badge = 15.0 if has_bestseller_badge else 0.0
    return max(0.0, min(100.0, vol * 0.5 + qual * 0.4 + badge))
