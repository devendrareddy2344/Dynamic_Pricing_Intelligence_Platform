"""
playwright_utils.py — Stealth Playwright page fetcher.

Uses playwright-stealth 2.0.3 (Stealth class, apply_stealth_async).
The best stealth approach is to apply it to the BrowserContext, not the Page,
because the Stealth class in 2.0.x registers init scripts on the context,
which run on every new page automatically.

Key hardening decisions:
- Blocks heavy resources (images, fonts, media) for faster loads.
- Adds realistic human-like delays and scroll behaviour.
- Passes locale/timezone/geolocation fingerprints matching the target site region.
- Gracefully degrades if stealth import fails.
"""
import asyncio
import random
from typing import Any, Sequence

from playwright.async_api import BrowserContext, Page, async_playwright

# playwright-stealth 2.0.3 API
try:
    from playwright_stealth.stealth import Stealth as _Stealth
    _STEALTH_CLS = _Stealth
except ImportError:
    _STEALTH_CLS = None


async def _apply_stealth(context: BrowserContext, user_agent: str) -> None:
    """Apply playwright-stealth 2.0.x evasions to the browser context."""
    if _STEALTH_CLS is None:
        return
    try:
        stealth = _STEALTH_CLS(
            navigator_user_agent_override=user_agent,
            navigator_platform_override="Win32",
            navigator_vendor_override="Google Inc.",
            webgl_vendor_override="Intel Inc.",
            webgl_renderer_override="Intel Iris OpenGL Engine",
        )
        await stealth.apply_stealth_async(context)
    except Exception:
        # Stealth is best-effort; never block scraping for it
        pass


async def _block_resources(page: Page) -> None:
    """Abort image/media/font/stylesheet requests to speed up page loads."""
    _BLOCKED = {"image", "media", "font", "stylesheet"}

    async def _handler(route):
        try:
            if route.request.resource_type in _BLOCKED:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass

    try:
        await page.route("**/*", _handler)
    except Exception:
        pass


async def fetch_page_html_with_stealth(
    url: str,
    user_agent: str,
    locale: str,
    viewport: Any,
    wait_selectors: Sequence[str] | None = None,
    timeout: int = 60000,
    proxy_url: Any | None = None,
    block_resources: bool = False,
) -> str | None:
    """Fetch HTML with Playwright + stealth. Returns None on any failure.

    Steps:
      1. Launch Chromium with anti-automation CLI flags.
      2. Create a context with realistic locale/timezone/geolocation.
      3. Apply playwright-stealth 2.0.3 evasions to the context.
      4. Navigate, optionally wait for product card selectors.
      5. Simulate a human scroll before capturing HTML.
    """
    is_india = "IN" in locale.upper()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=VizDisplayCompositor",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-zygote",
                "--disable-infobars",
                "--window-size=1280,800",
                f"--lang={locale.replace('_', '-')}",
            ],
        )

        extra_headers: dict[str, str] = {
            "Accept-Language": locale.replace("_", "-") + ",en;q=0.9",
        }

        context = await browser.new_context(
            user_agent=user_agent,
            locale=locale.replace("_", "-"),
            viewport=viewport,
            ignore_https_errors=True,
            proxy=proxy_url,
            extra_http_headers=extra_headers,
            timezone_id="Asia/Kolkata" if is_india else "America/New_York",
            geolocation=(
                {"latitude": 12.9716, "longitude": 77.5946}   # Bangalore
                if is_india
                else {"latitude": 40.7128, "longitude": -74.0060}  # New York
            ),
            permissions=["geolocation"],
        )

        # Apply stealth BEFORE creating the first page
        await _apply_stealth(context, user_agent)

        page = await context.new_page()

        # Block heavy resources for speed
        if block_resources:
            await _block_resources(page)

        try:
            page.set_default_navigation_timeout(timeout)
            page.set_default_timeout(timeout)

            from scraping_service.debug import log_debug
            log_debug(f"[{url}] playwright setup complete, navigating...")
            # Navigate — try domcontentloaded first, then commit as fallback
            try:
                r = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
                log_debug(f"[{url}] goto domcontentloaded status: {r.status if r else 'None'}")
            except Exception as e:
                log_debug(f"[{url}] goto domcontentloaded failed: {e}")
                try:
                    r = await page.goto(url, timeout=timeout, wait_until="commit")
                    log_debug(f"[{url}] goto commit status: {r.status if r else 'None'}")
                except Exception as e:
                    log_debug(f"[{url}] goto commit failed: {e}")
                    return None

            # Brief realistic pause after navigation completes
            await asyncio.sleep(random.uniform(0.5, 1.3))

            if wait_selectors:
                try:
                    # Combine all CSS selectors and wait for any to appear within a strict limit.
                    # This prevents wasting 30+ seconds if we land on a bot-block challenge page.
                    combined_sel = ", ".join(wait_selectors)
                    await page.wait_for_selector(combined_sel, timeout=10000)
                except Exception:
                    pass

            # Gradual human-like scroll to trigger lazy-loaded content
            await page.evaluate("window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.4))")
            await asyncio.sleep(random.uniform(0.3, 0.6))
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(random.uniform(0.4, 0.9))

            content = await page.content()
            log_debug(f"[{url}] playwright finished rendering. HTML size: {len(content)}")
            return content

        finally:
            for obj in (page, context, browser):
                try:
                    await obj.close()
                except Exception:
                    pass
