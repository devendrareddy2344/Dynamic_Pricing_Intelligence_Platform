import httpx
import logging

logging.basicConfig(level=logging.INFO)

def run_tests():
    base_url = "http://127.0.0.1:8000"
    client = httpx.Client(timeout=10.0)
    
    print("=== Testing Endpoints ===")
    
    # 1. Test Health
    try:
        res = client.get(f"{base_url}/health")
        print(f"GET /health -> Status: {res.status_code}")
        print(f"Response: {res.json()}")
    except Exception as e:
        print(f"GET /health -> Failed: {e}")

    # 2. Test Observability (Queries the Database via db.py)
    try:
        res = client.get(f"{base_url}/observability/overview")
        print(f"\nGET /observability/overview -> Status: {res.status_code}")
        print(f"Response: {res.json()}")
    except Exception as e:
        print(f"GET /observability/overview -> Failed: {e}")

if __name__ == "__main__":
    run_tests()
