import asyncio
import sys
import os
# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_service.scrapers.bestbuy import scrape_bestbuy

async def test():
    result = await scrape_bestbuy('iPhone 15', 'iPhone 15', 'test')
    print('Result:', result)

if __name__ == '__main__':
    asyncio.run(test())