#!/usr/bin/env python3
"""Test script to verify all imports work correctly."""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test all the problematic imports."""
    try:
        # Test scraping service imports
        from scraping_service.scrapers import SCRAPER_FUNCS
        print(f"✓ SCRAPER_FUNCS loaded: {sorted(SCRAPER_FUNCS.keys())}")

        from scraping_service.user_agents import random_ua, random_mobile_ua
        print("✓ scraping_service.user_agents imported")

        from scraping_service.scrapers.headers import get_browser_headers, get_mobile_headers
        print("✓ scraping_service.scrapers.headers imported")

        from scraping_service.scrapers.bestbuy import scrape_bestbuy
        print("✓ scraping_service.scrapers.bestbuy imported")

        # Test ML service imports
        from ml_service.analyser import analyse_prices
        print("✓ ml_service.analyser imported")

        # Test genai service imports
        from genai_service.recommender import stream_pricing_recommendation
        print("✓ genai_service.recommender imported")

        print("\n🎉 All imports successful!")
        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)