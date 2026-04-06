import asyncio
import os
import sys

from dotenv import load_dotenv

# Load .env variables
load_dotenv(".env")

# Ensure Python can find our custom module
sys.path.append(os.path.abspath("api_gateway"))

from api_gateway.db import get_pool


async def main():
    print("Attempting to connect to PostgreSQL...")
    pool = None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            version = await conn.fetchval('SELECT version()')
        
        async with pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM price_history')
            
        print("\n✅ SUCCESS! Database connected perfectly.")
        print(f"🎯 PostgreSQL Engine: {version}")
        print(f"📊 Table 'price_history' ready (Row count: {count})\n")
    except Exception as e:
        print(f"\n❌ FAILED! Could not connect to Database. Error Details:\n{e}\n")
        import traceback
        traceback.print_exc()
    finally:
        if pool:
            await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
