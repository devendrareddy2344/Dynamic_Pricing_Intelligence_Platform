#!/usr/bin/env python3
"""Quick test of scraper latency after optimizations."""

import asyncio
import time
import sys
import os

# Add scraping service to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scraping-service"))

from scraping_service.scrapers.flipkart import scrape_flipkart
from scraping_service.scrapers.bestbuy import scrape_bestbuy
from scraping_service.scrapers.croma import scrape_croma
from scraping_service.scrapers.walmart import scrape_walmart
from scraping_service.scrapers.amazon import scrape_amazon


async def test_scraper_latency():
    """Test that scrapers complete quickly without timeout issues."""
    
    test_cases = [
        ("flipkart", scrape_flipkart, "Galaxy S26 Ultra", "Galaxy S26 Ultra smartphone"),
        ("bestbuy", scrape_bestbuy, "Samsung Galaxy S26 Ultra", "Galaxy S26 Ultra smartphone"),
        ("croma", scrape_croma, "Galaxy S26 Ultra", "Galaxy S26 Ultra smartphone"),
        ("walmart", scrape_walmart, "Samsung Galaxy S26 Ultra", "Galaxy S26 Ultra smartphone"),
        ("amazon", scrape_amazon, "Samsung Galaxy S26 Ultra", "Galaxy S26 Ultra smartphone"),
    ]
    
    session_id = "test-latency-check"
    results = {}
    
    for name, scraper_fn, query, product_name in test_cases:
        print(f"\n{'='*60}")
        print(f"Testing {name.upper()} - Query: {query}")
        print(f"{'='*60}")
        
        t0 = time.perf_counter()
        try:
            # Set 25-second timeout to match orchestrator
            offer = await asyncio.wait_for(
                scraper_fn(query, product_name, session_id),
                timeout=25.0
            )
            elapsed = (time.perf_counter() - t0) * 1000
            
            if offer:
                print(f"✓ SUCCESS in {elapsed:.1f}ms")
                print(f"  Price: {offer.currency} {offer.price}")
                print(f"  Product: {offer.product_title[:60]}...")
                print(f"  Match Score: {offer.title_match_score:.2f}")
                results[name] = {"status": "ok", "latency_ms": elapsed, "price": offer.price}
            else:
                print(f"! NO OFFER in {elapsed:.1f}ms")
                results[name] = {"status": "no_offer", "latency_ms": elapsed}
                
        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"✗ TIMEOUT after {elapsed:.1f}ms (>25s limit)")
            results[name] = {"status": "timeout", "latency_ms": elapsed}
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"✗ ERROR after {elapsed:.1f}ms: {type(e).__name__}: {str(e)[:100]}")
            results[name] = {"status": "error", "latency_ms": elapsed, "error": str(e)[:100]}
    
    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    for name, result in results.items():
        status = result["status"].upper()
        latency = result["latency_ms"]
        
        if status == "OK":
            symbol = "✓"
            color_start = "\033[92m"  # Green
        elif status == "TIMEOUT":
            symbol = "✗"
            color_start = "\033[91m"  # Red
        else:
            symbol = "!"
            color_start = "\033[93m"  # Yellow
        
        color_end = "\033[0m"
        print(f"{color_start}{symbol} {name:12} {status:10} {latency:7.1f}ms{color_end}")


if __name__ == "__main__":
    asyncio.run(test_scraper_latency())
