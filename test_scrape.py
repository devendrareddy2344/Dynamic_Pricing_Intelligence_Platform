import httpx
import json
import asyncio

async def test_scraping():
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                'http://127.0.0.1:8000/scrape',
                json={'query': 'iPhone 15', 'session_id': 'test123'}
            )
            print(f'Status: {response.status_code}')
            if response.status_code == 200:
                data = response.json()
                print('Scraping results:')
                for offer in data.get('offers', []):
                    print(f'  {offer["source"]}: {offer["price"]} {offer["currency"]} - {offer["product_title"][:50]}...')
            else:
                print(f'Error: {response.text}')
        except Exception as e:
            print(f'Exception: {e}')

if __name__ == "__main__":
    asyncio.run(test_scraping())