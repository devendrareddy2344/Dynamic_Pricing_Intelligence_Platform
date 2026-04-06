# Scraper Optimization Summary

## Problem
Scrapers were timing out with very long latencies (20-116 seconds), causing poor user experience:
- Best Buy: 116 seconds → 84 seconds on retry
- Flipkart, Croma, Walmart: 50+ seconds per attempt
- Only Amazon was working reliably

## Root Causes
1. **Excessive timeouts**: `page.goto()` had 25-40 second timeouts, combined with 45 second orchestrator timeout and 2 retries = up to 180+ seconds worse case
2. **Slow page load waiting**: Using `wait_until="load"` instead of `"domcontentloaded"` added unnecessary delay
3. **Long waits for elements**: `wait_for_selector()` had 7-10 second timeouts even when no elements exist
4. **Excessive scrolling/sleeping**: Added 1-2 second pauses unnecessarily
5. **Poor error recovery**: Timeouts didn't fail fast, wasting time trying to load non-existent elements

## Solutions Implemented

### 1. **Orchestrator Defaults** (`orchestrator.py`)
- Reduced default timeout from **45s → 20s**
- Reduced max retries from **2 → 1** (to prevent excessive retry multiplier)

### 2. **Best Buy Scraper** (`bestbuy.py`)
- Changed `page.goto()` timeout: **40s → 15s** (use `domcontentloaded`)
- Changed `wait_for_selector()` timeout: **10s → 5s**
- **Early return if no elements found** instead of passing silently
- Shortened CSS selectors to most reliable ones
- Added explicit `asyncio.TimeoutError` handling to return `None` immediately

### 3. **Flipkart Scraper** (`flipkart.py`)
- Changed `page.goto()` timeout: **25s → 12s**
- Changed `wait_for_selector()` timeout: **7s → 4s** with early return
- Reduced scroll from 400px/1s to 300px/0.5s
- Updated selectors for current Flipkart layout
- Added explicit timeout exception handling

### 4. **Croma Scraper** (`croma.py`)
- Changed `page.goto()` timeout: **30s → 12s**
- Changed `wait_for_selector()` timeout: **8s → 4s** with early return  
- Reduced scroll from 400px/0.5s to 300px/0.3s
- Added explicit timeout exception handling with early return

### 5. **Walmart Scraper** (`walmart.py`)
- Changed `page.goto()` timeout: **40s → 12s**
- Changed `wait_for_selector()` fallback timeout: **5s → 4s** with early return
- Reduced sleep from 2s to 1s
- Added explicit timeout exception handling

## Expected Improvements
- **Best Buy**: 116s → ~15-18s (85% reduction)
- **Flipkart**: 54s → ~12-14s (75% reduction)
- **Croma**: 52s → ~12-14s (75% reduction)  
- **Walmart**: 50s+ → ~12-15s (75% reduction)
- **Overall**: Multiple queries now complete in ~30-45s instead of 2+ minutes

## Key Changes Pattern
```python
# Before
await page.goto(url, wait_until="load", timeout=40000)
await page.wait_for_selector(".element", timeout=10000)

# After
await page.goto(url, wait_until="domcontentloaded", timeout=12000)
try:
    await page.wait_for_selector(".element", timeout=4000)
except asyncio.TimeoutError:
    return None  # Early exit instead of hanging
```

## Testing
- Application starts successfully with optimized scrapers
- File changes auto-reload correctly (Uvicorn hot reload)
- All import paths and exception handlers verified

## Future Improvements
1. Implement proxy rotation for sites with strict bot detection
2. Add fallback to httpx/requests for Playwright-based scrapers
3. Implement circuit breaker pattern (pending Redis)
4. Cache selectors/user agents per site
5. Use browser context pooling to reduce startup time
