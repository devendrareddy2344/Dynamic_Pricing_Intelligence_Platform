from rapidfuzz import fuzz


def title_match_score(identified_product: str, scraped_title: str) -> float:
    if not scraped_title:
        return 0.0
    return fuzz.token_set_ratio(identified_product.lower(), scraped_title.lower()) / 100.0


def passes_validation(score: float, threshold: float = 0.70) -> bool:
    return score >= threshold
