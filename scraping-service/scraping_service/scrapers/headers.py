import random

def get_browser_headers(site: str = "generic") -> dict[str, str]:
    """Return a robust set of browser-like headers to minimize bot detection."""
    
    # Common desktop platforms
    platforms = [
        ("Windows NT 10.0; Win64; x64", "Windows"),
        ("Macintosh; Intel Mac OS X 10_15_7", "macOS"),
        ("X11; Linux x86_64", "Linux")
    ]
    platform_str, oscpu = random.choice(platforms)
    
    # Common Chrome versions
    chrome_versions = ["118.0.0.0", "119.0.0.0", "120.0.0.0", "121.0.0.0"]
    chrome_ver = random.choice(chrome_versions)
    main_ver = chrome_ver.split('.')[0]
    
    ua = f"Mozilla/5.0 ({platform_str}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36"
    
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": f'"Not_A Brand";v="8", "Chromium";v="{main_ver}", "Google Chrome";v="{main_ver}"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": f'"{oscpu}"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": f'"{chrome_ver}"',
        "sec-ch-ua-full-version-list": f'"Not_A Brand";v="8.0.0.0", "Chromium";v="{chrome_ver}", "Google Chrome";v="{chrome_ver}"',
        "sec-ch-ua-platform-version": '"10.0.0"',
        "DNT": "1",
        "Cache-Control": "max-age=0",
    }
    
    if site == "amazon":
        headers["Accept-Language"] = "en-US,en;q=0.5"
        headers["Referer"] = "https://www.google.com/"
    elif site == "croma" or site == "flipkart" or site == "tatacliq":
        headers["Accept-Language"] = "en-IN,en;q=0.9"
        headers["Referer"] = f"https://www.{site}.com/"
        
    return headers

def get_mobile_headers(site: str = "generic") -> dict[str, str]:
    """Return mobile browser headers for mobile-first pages and stricter bot checks."""
    ua = "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    accept_language = "en-US,en;q=0.9"
    referer = "https://www.google.com/"

    if site in {"croma", "flipkart", "tatacliq"}:
        accept_language = "en-IN,en;q=0.9"
        referer = f"https://www.{site}.com/"
    elif site == "walmart":
        accept_language = "en-US,en;q=0.9"
        referer = "https://www.google.com/"
    elif site == "bestbuy":
        accept_language = "en-US,en;q=0.9"
        referer = "https://www.google.com/"

    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": accept_language,
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-CH-UA-Mobile": "?1",
        "Sec-CH-UA-Platform": '"Android"',
        "Referer": referer,
        "DNT": "1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua-arch": '"arm"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"120.0.5993.112"',
        "sec-ch-ua-full-version-list": '"Not_A Brand";v="8.0.0.0", "Chromium";v="120.0.5993.112", "Google Chrome";v="120.0.5993.112"',
        "sec-ch-ua-model": '"SM-G998B"',
        "sec-ch-ua-platform-version": '"13.0.0"',
    }
