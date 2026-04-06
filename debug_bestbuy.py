import asyncio, sys, json
# Try to configure stdout encoding for Windows compatibility
reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
if callable(reconfigure_stdout):
    reconfigure_stdout(encoding="utf-8")
# Import path is now handled by pyproject.toml
from playwright.async_api import async_playwright
from scraping_service.user_agents import random_mobile_ua

async def main():
    url = 'https://www.bestbuy.com/site/searchpage.jsp?st=iPhone+15&intl=nosplash'
    ua = random_mobile_ua()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled','--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage'])
        context = await browser.new_context(user_agent=ua, locale='en-US', viewport={'width':1280,'height':800}, ignore_https_errors=True)
        page = await context.new_page()
        graphql_requests = []

        async def handle_request(request):
            if 'gateway/graphql' in request.url and request.method == 'POST':
                try:
                    body = await request.post_data()
                    if body:
                        data = json.loads(body)
                        graphql_requests.append({
                            'url': request.url,
                            'query': data.get('query', '')[:200] + '...' if len(data.get('query', '')) > 200 else data.get('query', ''),
                            'variables': data.get('variables', {})
                        })
                except:
                    pass

        page.on('request', handle_request)

        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(15000)  # Wait longer

        # Try to wait for product elements
        try:
            await page.wait_for_selector('[data-automation="product-item"]', timeout=10000)
        except:
            pass

        print('title', await page.title())
        print(f'captured GraphQL requests: {len(graphql_requests)}')

        for req in graphql_requests[:5]:  # Show first 5
            print(f"Query: {req['query']}")
            print(f"Variables: {req['variables']}")
            print('---')

        # Check for product data in page
        products = await page.query_selector_all('[data-automation="product-item"]')
        print(f'Found {len(products)} product items on page')

        if products:
            first_product = products[0]
            title = await first_product.query_selector('[data-automation="product-title"]')
            price = await first_product.query_selector('[data-automation="product-price"]')
            if title:
                print('Sample product title:', await title.inner_text())
            if price:
                print('Sample product price:', await price.inner_text())

        await browser.close()

asyncio.run(main())