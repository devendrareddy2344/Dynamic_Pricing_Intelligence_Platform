import os
import random
from httpx import AsyncHTTPTransport
from playwright.async_api import ProxySettings

# List of proxies (add real proxy URLs here)
# For testing, you can find free proxies online, but they are unreliable.
# For production, use a paid proxy service like Bright Data, Oxylabs, Smartproxy, etc.
# Format: "http://username:password@proxy_ip:port" or "http://proxy_ip:port"
PROXY_POOL = [
    # Free proxies (unreliable, for testing only)
    "http://185.82.99.181:9091",
    "http://190.103.177.131:80", 
    "http://181.78.22.158:999",
    "http://45.230.176.145:999",
    "http://200.106.184.11:999",
    # Add working proxies here
]

def get_random_proxy() -> str | None:
    """Return a random proxy from the pool."""
    if not PROXY_POOL:
        return None
    return random.choice(PROXY_POOL)

def get_site_proxy(site: str) -> str | None:
    """Return a dedicated proxy for the given site if configured, otherwise a random proxy."""
    env_key = f"{site.upper()}_PROXY"
    proxy_url = os.environ.get(env_key, "")
    if proxy_url:
        return proxy_url.strip()
    return get_random_proxy()

def get_proxy_transport(proxy_url: str | None) -> AsyncHTTPTransport | None:
    """Return httpx transport with proxy, or None if no proxy."""
    if not proxy_url:
        return None
    return AsyncHTTPTransport(proxy=proxy_url)

def get_playwright_proxy(proxy_url: str | None) -> ProxySettings | None:
    """Return proxy dict for Playwright browser."""
    if not proxy_url:
        return None

    # Parse proxy URL: http://user:pass@host:port or http://host:port
    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"

    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)

        proxy_dict = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        }

        if parsed.username and parsed.password:
            proxy_dict["username"] = parsed.username
            proxy_dict["password"] = parsed.password

        return proxy_dict
    except Exception:
        return None