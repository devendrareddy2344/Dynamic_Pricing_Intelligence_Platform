import asyncio
import os
import redis.asyncio as redis
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

async def test_redis():
    r = redis.from_url(os.environ.get("REDIS_URL", ""))
    try:
        await r.ping()
        print("Redis: OK")
    except Exception as e:
        print("Redis Error:", e)
    finally:
        await r.aclose()

def test_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Gemini: Missing API Key")
        return
    if api_key == "AIzaSyDHM-7BaqCTuolrFQX6OchYsUPdCgZEX7c":
        print("Gemini: Still using the fake placeholder key from .env.example!")
        return
    genai.configure(api_key=api_key)
    try:
        # Just fetching models to test auth
        models = [m.name for m in genai.list_models()]
        print("Gemini: OK. Connected successfully.")
    except Exception as e:
        print("Gemini Error:", e)

async def main():
    await test_redis()
    test_gemini()

if __name__ == "__main__":
    asyncio.run(main())
