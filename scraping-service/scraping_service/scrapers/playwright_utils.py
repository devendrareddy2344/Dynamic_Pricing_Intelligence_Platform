from typing import Sequence, Any
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth


async def fetch_page_html_with_stealth(
    url: str,
    user_agent: str,
    locale: str,
    viewport: Any,
    wait_selectors: Sequence[str] | None = None,
    timeout: int = 20000,
) -> str | None:
    """Fetch HTML with Playwright + stealth and wait for the page to render.

    The function returns the page HTML after waiting for at least one of the provided
    CSS selectors, or after navigation completes.
    """
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=VizDisplayCompositor",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=user_agent,
            locale=locale,
            viewport=viewport,
            ignore_https_errors=True,
        )
        page = await context.new_page()
        await page.goto(url, timeout=timeout, wait_until="domcontentloaded")

        if wait_selectors:
            for selector in wait_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=2000)
                    break
                except Exception:
                    continue
        
        # Wait additional time and scroll to load dynamic content (keep short to fit timeout budget)
        await page.wait_for_timeout(1000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)
        
        html = await page.content()
        await page.close()
        await context.close()
        await browser.close()
        return html
