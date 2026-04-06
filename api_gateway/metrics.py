from __future__ import annotations

import time
from typing import Any

# Optional prometheus imports
try:
    from prometheus_client import Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Create dummy classes that do nothing
    class DummyMetric:
        def __init__(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def inc(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self

    Counter = Gauge = Histogram = DummyMetric

vision_latency = Histogram(
    "vision_ai_latency_ms",
    "Vision AI identification latency",
    buckets=(50, 100, 250, 500, 1000, 2000, 4000, 8000, 15000),
)
vision_confidence = Histogram(
    "vision_ai_confidence_score",
    "Vision confidence scores",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)
scraper_latency = Histogram(
    "scraper_latency_ms",
    "Per-scraper duration",
    ["site_name"],
    buckets=(100, 500, 1000, 5000, 10000, 15000, 20000, 25000, 30000),
)
scraper_success_rate = Gauge(
    "scraper_success_rate",
    "Rolling success rate placeholder (updated per batch)",
)
scraper_failures_total = Counter(
    "scraper_failures_total",
    "Scraper failures",
    ["reason", "site_name"],
)
price_anomaly_rate = Gauge("price_anomaly_rate", "Fraction of prices flagged anomalous")
ml_analysis_latency_ms = Histogram(
    "ml_analysis_latency_ms",
    "ML clustering and analysis time",
    buckets=(10, 50, 100, 250, 500, 1000, 2000, 5000),
)
genai_time_to_first_token_ms = Gauge(
    "genai_time_to_first_token_ms",
    "Time from ML ready to first GenAI token",
)
end_to_end_query_latency_ms = Histogram(
    "end_to_end_query_latency_ms",
    "Upload to first price card (client-side join in Grafana)",
    buckets=(1000, 2000, 5000, 8000, 10000, 15000, 20000, 30000, 60000),
)
price_history_records_total = Counter(
    "price_history_records_total",
    "Rows written to TimescaleDB",
)


def observe_scraper_event(ev: dict[str, Any]) -> None:
    if ev.get("event") == "price_scraped":
        site = ev.get("source", "unknown")
        lat = float(ev.get("latency_ms") or 0)
        if lat > 0:
            scraper_latency.labels(site_name=site).observe(lat)
    elif ev.get("event") == "scraper_failed":
        scraper_failures_total.labels(
            reason=str(ev.get("reason", "unknown"))[:40],
            site_name=ev.get("source", "unknown"),
        ).inc()
