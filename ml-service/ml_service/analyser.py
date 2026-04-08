from __future__ import annotations

import os
from typing import Any, List

import numpy as np
import pandas as pd
import warnings

from sklearn.cluster import KMeans, DBSCAN, AgglomerativeClustering
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import ConvergenceWarning

from ml_service.demand import demand_score


def analyse_prices(
    offers: list[dict[str, Any]],
    min_sources: int | None = None,
    model_type: str = "auto",
) -> dict[str, Any]:
    """
    Enhanced price analysis with multiple ML models.

    Args:
        offers: List of offer dictionaries
        min_sources: Minimum sources required for analysis
        model_type: ML model to use ('auto', 'kmeans', 'dbscan', 'hierarchical', 'robust')
    """
    min_s = min_sources or int(os.environ.get("MIN_SOURCES_FOR_ML", "1"))
    if len(offers) < min_s:
        return {
            "ready": False,
            "reason": f"need_at_least_{min_s}_sources",
            "count": len(offers),
        }

    df = pd.DataFrame(offers)
    prices = df["price"].astype(float).to_numpy(dtype=float).reshape(-1, 1)

    # Enhanced anomaly detection with multiple methods
    anomalies = _detect_anomalies(prices, model_type)
    df = df.assign(is_anomaly=anomalies)

    clean = df[~df["is_anomaly"]].copy()
    if len(clean) < min_s:
        clean = df.copy()

    # Choose clustering algorithm based on model_type
    clusters, cluster_centers, tier_map = _cluster_prices(clean["price"].to_numpy(dtype=float), model_type)

    clean = clean.assign(cluster=clusters)

    rows_out = []
    for _, row in clean.iterrows():
        tier = tier_map.get(int(row["cluster"]), "mid")
        rows_out.append(
            {
                "source": row.get("source"),
                "price": float(row["price"]),
                "cluster_tier": tier,
                "is_anomaly": bool(row["is_anomaly"]),
            }
        )

    pmin = float(clean["price"].min())
    pmax = float(clean["price"].max())
    pavg = float(clean["price"].mean())

    # Dynamic pricing strategy based on market conditions
    target, strategy = _calculate_optimal_price(clean["price"].to_numpy(dtype=float), pavg, pmin, pmax)

    low = float(round(pmin * 0.97, 2))
    high = float(round(min(pmax * 1.02, pavg * 1.08), 2))

    dscores = [
        demand_score(
            o.get("review_count"),
            o.get("seller_rating"),
            o.get("bestseller") is True,
        )
        for o in offers
    ]
    demand_avg = float(np.mean(dscores)) if dscores else 50.0

    competitive = max(
        0.0,
        min(100.0, 100.0 * (1.0 - (target - pmin) / max(pmax - pmin, 1e-6))),
    )

    # Additional ML insights
    volatility = _calculate_price_volatility(clean["price"].to_numpy(dtype=float))
    trend_direction = _analyze_price_trend(clean["price"].to_numpy(dtype=float))

    return {
        "ready": True,
        "market_min": pmin,
        "market_max": pmax,
        "market_avg": pavg,
        "recommended_price": target,
        "price_range": {"low": low, "high": high},
        "strategy": strategy,
        "competitive_score": round(competitive, 2),
        "demand_score": round(demand_avg, 2),
        "volatility_score": round(volatility, 2),
        "trend_direction": trend_direction,
        "clusters": cluster_centers.tolist() if cluster_centers is not None else [],
        "model_used": model_type,
        "offers_detail": rows_out,
        "confidence_level": _calculate_confidence(len(offers), volatility, demand_avg),
    }


def _detect_anomalies(prices: np.ndarray, model_type: str) -> np.ndarray:
    """Detect price anomalies using different methods."""
    if model_type == "robust":
        # Use Local Outlier Factor for more robust anomaly detection
        lof = LocalOutlierFactor(n_neighbors=min(20, len(prices)-1), contamination=0.15)
        return lof.fit_predict(prices) == -1
    else:
        # Default Isolation Forest
        iso = IsolationForest(contamination=0.15, random_state=42)
        iso.fit(prices)
        return iso.predict(prices) == -1


def _cluster_prices(prices: np.ndarray, model_type: str) -> tuple[np.ndarray, np.ndarray | None, dict[int, str]]:
    """Cluster prices using different algorithms."""
    X = prices.reshape(-1, 1)
    # Never request more clusters than distinct price values — prevents k-means
    # ConvergenceWarning: "Number of distinct clusters found smaller than n_clusters"
    n_distinct = len(np.unique(prices))
    k = min(3, len(prices), max(1, n_distinct))

    # Handle single-cluster scenarios gracefully (fast path)
    if k == 1 or np.std(prices) < 1e-4:
        labels = np.zeros(len(prices), dtype=int)
        centers = np.array([prices.mean()])
        return labels, centers, {0: "mid"}

    if model_type == "dbscan":
        # DBSCAN for density-based clustering
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        db = DBSCAN(eps=0.5, min_samples=2)
        labels = db.fit_predict(X_scaled)
        # Convert DBSCAN labels to tier mapping
        unique_labels = np.unique(labels[labels != -1])
        if len(unique_labels) == 0:
            labels = np.zeros(len(prices), dtype=int)
            centers = np.array([prices.mean()])
        else:
            centers = np.array([prices[labels == label].mean() for label in unique_labels])
            # Remap noise points (-1) to nearest cluster
            for i, label in enumerate(labels):
                if label == -1:
                    distances = np.abs(prices[i] - centers)
                    labels[i] = np.argmin(distances)

    elif model_type == "hierarchical":
        # Hierarchical clustering
        hc = AgglomerativeClustering(n_clusters=k, linkage='ward')
        labels = hc.fit_predict(X)
        centers = np.array([prices[labels == i].mean() for i in range(k)])

    else:  # default kmeans or auto
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(X)
            centers = km.cluster_centers_.flatten()

    # Create tier mapping
    tiers = ["budget", "mid", "premium"]
    if centers is not None:
        cluster_order = np.argsort(centers)
        tier_map = {
            int(cluster_order[i]): tiers[i] if i < len(tiers) else f"tier_{i}"
            for i in range(len(cluster_order))
        }
    else:
        tier_map = {i: tiers[i] if i < len(tiers) else f"tier_{i}" for i in range(k)}

    return labels, centers, tier_map


def _calculate_optimal_price(prices: np.ndarray, pavg: float, pmin: float, pmax: float) -> tuple[float, str]:
    """Calculate optimal pricing strategy."""
    # Use statistical measures for better pricing
    median_price = float(np.median(prices))
    q25, q75 = np.percentile(prices, [25, 75])
    q25, q75 = float(q25), float(q75)
    iqr = q75 - q25

    # Dynamic target based on distribution
    if iqr / pavg > 0.3:  # High variance market
        target = float(round(pavg * 0.95, 2))  # More aggressive
        strategy = "penetration"
    elif median_price < pavg * 0.9:  # Skewed distribution
        target = float(round(median_price * 1.02, 2))
        strategy = "competitive"
    else:
        target = float(round(pavg * 0.98, 2))
        strategy = "competitive"

    # Premium strategy for high-end products
    if target > pavg * 1.05:
        strategy = "premium"

    return target, strategy


def _calculate_price_volatility(prices: np.ndarray) -> float:
    """Calculate price volatility score."""
    if len(prices) < 2:
        return 0.0

    returns = np.diff(prices) / prices[:-1]
    volatility = float(np.std(returns) * 100)  # As percentage
    return float(min(volatility, 100.0))  # Cap at 100


def _analyze_price_trend(prices: np.ndarray) -> str:
    """Analyze price trend direction."""
    if len(prices) < 3:
        return "stable"

    # Simple linear trend
    x = np.arange(len(prices))
    slope = np.polyfit(x, prices, 1)[0]

    if slope > prices.mean() * 0.01:
        return "increasing"
    elif slope < -prices.mean() * 0.01:
        return "decreasing"
    else:
        return "stable"


def _calculate_confidence(num_offers: int, volatility: float, demand_score: float) -> float:
    """Calculate overall confidence in the analysis."""
    # Base confidence from sample size
    size_conf = min(num_offers / 10.0, 1.0) * 100

    # Adjust for volatility (lower volatility = higher confidence)
    vol_penalty = volatility / 2

    # Adjust for demand score
    demand_bonus = (demand_score - 50) / 2

    confidence = size_conf - vol_penalty + demand_bonus
    return max(0.0, min(100.0, confidence))
