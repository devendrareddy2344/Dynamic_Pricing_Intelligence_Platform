import random

# List of proxies (add real proxy URLs here)
# For testing, you can find free proxies online, but they are unreliable.
# For production, use a paid proxy service like Bright Data, Oxylabs, Smartproxy, etc.
# Format: "http://username:password@proxy_ip:port" or "http://proxy_ip:port"
PROXY_POOL = [
    # Example: "http://123.45.67.89:8080",
    # Add your proxies here
]

def get_random_proxy() -> str:
    """Return a random proxy from the pool."""
    if not PROXY_POOL:
        return None
    return random.choice(PROXY_POOL)

def get_proxy_dict(proxy_url: str) -> dict:
    """Return proxy dict for httpx."""
    if not proxy_url:
        return None
    return {
        "http://": proxy_url,
        "https://": proxy_url,
    }