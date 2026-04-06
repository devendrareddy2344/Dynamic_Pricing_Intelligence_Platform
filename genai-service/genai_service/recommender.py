from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, List
import httpx
from dotenv import load_dotenv

from ml_service.analyser import analyse_prices

load_dotenv(override=True)

SYSTEM_TEMPLATE = """You are a senior pricing strategist for the Indian e-commerce market. Output clear, structured Markdown with exactly these sections and headings in order:

## Market Summary
## Recommended Selling Price
## Pricing Strategy
## Risk Factors
## Confidence Level

CRITICAL RULES:
- ALL prices MUST be displayed in Indian Rupees (₹ INR). Never use USD or any other currency.
- If you see prices in USD format, convert them at approximately ₹83.5 per USD before displaying.
- Reference the Indian market context (Flipkart, Amazon India, Croma, etc.) where relevant.
- Be concise. Use bullet points where helpful. Reference actual ₹ numbers from the data provided.
- Consider the ML model's analysis quality and confidence level when making recommendations.
- Use advanced pricing psychology and market positioning strategies."""

ENHANCED_SYSTEM = """You are an expert AI pricing strategist with deep knowledge of e-commerce dynamics, consumer behavior, and advanced ML-driven market analysis. You excel at synthesizing complex data into actionable pricing recommendations.

Your expertise includes:
- Advanced statistical analysis and ML model interpretation
- Dynamic pricing strategies based on market volatility and trends
- Competitive positioning and market share optimization
- Risk assessment and confidence scoring
- Cross-platform e-commerce analysis (Amazon, Flipkart, Walmart, Best Buy, etc.)

Always provide data-driven, evidence-based recommendations with clear reasoning."""

def select_best_ml_model(product: dict[str, Any], offers: list[dict[str, Any]]) -> str:
    """Select the most appropriate ML model based on product and market characteristics."""
    num_offers = len(offers)
    if num_offers < 5:
        return "kmeans"  # Simple clustering for small datasets

    # Check price distribution
    prices = [o.get("price", 0) for o in offers]
    price_range = max(prices) - min(prices)
    avg_price = sum(prices) / len(prices)

    # High variance suggests complex market structure
    if price_range / avg_price > 0.5:
        return "hierarchical"  # Better for complex hierarchies

    # Check for outliers (potential different market segments)
    sorted_prices = sorted(prices)
    q1, q3 = sorted_prices[len(sorted_prices)//4], sorted_prices[3*len(sorted_prices)//4]
    iqr = q3 - q1

    if any(p < q1 - 1.5*iqr or p > q3 + 1.5*iqr for p in prices):
        return "dbscan"  # Better for detecting outliers and clusters

    # Check product category for specialized analysis
    product_name = product.get("name", "").lower()
    if any(keyword in product_name for keyword in ["phone", "laptop", "tv", "camera"]):
        return "robust"  # Electronics often have distinct tiers

    return "auto"  # Let the system decide

def _build_enhanced_user_prompt(
    *,
    product: dict[str, Any],
    offers: list[dict[str, Any]],
    ml: dict[str, Any],
    selected_model: str,
) -> str:
    """Build an enhanced prompt that leverages advanced ML analysis."""

    # Analyze market conditions
    market_context = _analyze_market_context(offers, ml)

    # Generate strategic insights
    strategic_insights = _generate_strategic_insights(product, offers, ml)

    return f"""# Advanced Pricing Analysis Request

## Product Information
{json.dumps(product, indent=2)}

## Market Data & Competitive Landscape
{json.dumps(offers, indent=2)}

## Advanced ML Analysis Results
**Model Used:** {selected_model}
**Analysis Details:**
{json.dumps(ml, indent=2)}

## Market Intelligence
{market_context}

## Strategic Considerations
{strategic_insights}

## Analysis Instructions
Based on the above comprehensive data, provide a sophisticated pricing recommendation that considers:

1. **Market Summary**: Analyze competitive positioning, price distribution, and market dynamics
2. **Recommended Selling Price**: Suggest optimal price with detailed justification considering ML insights, market volatility, and competitive landscape
3. **Pricing Strategy**: Choose from penetration/competitive/premium strategies with evidence-based reasoning
4. **Risk Factors**: Identify specific risks including data quality, market volatility, competitive threats, and external factors
5. **Confidence Level**: Rate analysis confidence (0-100) based on data quality, sample size, and ML model reliability

Use advanced pricing psychology, consider seasonal factors, and leverage the ML model's sophisticated analysis for data-driven decisions."""

def _analyze_market_context(offers: list[dict[str, Any]], ml: dict[str, Any]) -> str:
    """Analyze market context for better pricing decisions."""
    sources = list(set(o.get("source", "unknown") for o in offers))
    num_sources = len(sources)

    volatility = ml.get("volatility_score", 0)
    trend = ml.get("trend_direction", "stable")
    competitive_score = ml.get("competitive_score", 50)

    context = f"""
- **Market Coverage**: {num_sources} sources analyzed ({', '.join(sources)})
- **Price Volatility**: {volatility:.1f}% (market stability indicator)
- **Price Trend**: {trend} (market direction)
- **Competitive Position**: {competitive_score:.1f}/100 (relative positioning)
- **Data Quality**: {ml.get('confidence_level', 50):.1f}% confidence in analysis
"""

    if volatility > 20:
        context += "- **High Volatility Alert**: Prices fluctuate significantly - consider dynamic pricing\n"
    if trend == "increasing":
        context += "- **Upward Trend**: Market prices rising - opportunity for premium positioning\n"
    elif trend == "decreasing":
        context += "- **Downward Trend**: Market prices falling - consider competitive or penetration strategies\n"

    return context

def _generate_strategic_insights(product: dict[str, Any], offers: list[dict[str, Any]], ml: dict[str, Any]) -> str:
    """Generate strategic insights for pricing decisions."""
    insights = []

    # Product category analysis
    product_name = product.get("name", "").lower()
    if any(kw in product_name for kw in ["iphone", "samsung", "pixel"]):
        insights.append("- **Premium Electronics**: Consider brand loyalty and ecosystem effects")
    elif any(kw in product_name for kw in ["budget", "basic", "entry"]):
        insights.append("- **Budget Segment**: Focus on value proposition and feature comparison")

    # Market saturation analysis
    num_offers = len(offers)
    if num_offers > 10:
        insights.append("- **High Competition**: Market saturated - differentiation crucial")
    elif num_offers < 5:
        insights.append("- **Limited Data**: Analysis based on sparse market data - exercise caution")

    # Pricing strategy hints
    strategy = ml.get("strategy", "competitive")
    if strategy == "penetration":
        insights.append("- **Penetration Strategy**: Recommended for market share growth and new product launches")
    elif strategy == "premium":
        insights.append("- **Premium Strategy**: Suitable for differentiated or luxury products")

    return "\n".join(insights) if insights else "- **Standard Market Conditions**: Apply conventional competitive pricing"

async def stream_pricing_recommendation(
    product: dict[str, Any],
    offers: list[dict[str, Any]],
    ml: dict[str, Any],
    use_enhanced: bool = True,
) -> AsyncIterator[str]:
    """Enhanced pricing recommendation with advanced ML model selection."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment or .env file")

    # Select best ML model for this analysis
    selected_model = select_best_ml_model(product, offers)

    # Re-run ML analysis with selected model if different
    if ml.get("model_used") != selected_model:
        ml = analyse_prices(offers, model_type=selected_model)

    model_name = os.environ.get("OPENROUTER_TEXT_MODEL", "google/gemini-2.0-flash-exp:free")

    # Choose system prompt
    system_prompt = ENHANCED_SYSTEM if use_enhanced else SYSTEM_TEMPLATE

    # Build enhanced user prompt
    user_prompt = _build_enhanced_user_prompt(
        product=product,
        offers=offers,
        ml=ml,
        selected_model=selected_model
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Synycs Dynamic Pricing"
    }

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": True,
        "temperature": 0.35,
        "max_tokens": 4096
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError):
                        pass
