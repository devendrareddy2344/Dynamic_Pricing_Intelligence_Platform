import random


# Current Chrome versions (refresh quarterly)
_CHROME_VERSIONS = ["122.0.0.0", "123.0.0.0", "124.0.0.0", "125.0.0.0"]
_PLATFORMS = [
    ("Windows NT 10.0; Win64; x64", "Windows"),
    ("Macintosh; Intel Mac OS X 10_15_7", "macOS"),
    ("Macintosh; Intel Mac OS X 14_4_1", "macOS"),
    ("X11; Linux x86_64", "Linux"),
]


def get_browser_headers(site: str = "generic") -> dict[str, str]:
    """Return a robust set of browser-like headers to minimise bot detection."""
    platform_str, oscpu = random.choice(_PLATFORMS)
    chrome_ver = random.choice(_CHROME_VERSIONS)
    main_ver = chrome_ver.split(".")[0]

    ua = (
        f"Mozilla/5.0 ({platform_str}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36"
    )

    # Locale / referer overrides per site
    accept_language = "en-US,en;q=0.9"
    referer = "https://www.google.com/"
    site_origin = None

    if site in {"croma", "flipkart", "tatacliq"}:
        accept_language = "en-IN,en;q=0.9,hi;q=0.8"
        referer = f"https://www.{site}.com/"
        site_origin = f"https://www.{site}.com"
    elif site == "amazon":
        accept_language = "en-US,en;q=0.5"
        referer = "https://www.google.com/"
    elif site in {"walmart", "bestbuy", "target", "ebay"}:
        accept_language = "en-US,en;q=0.9"
        referer = "https://www.google.com/"

    headers: dict[str, str] = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;"
            "q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": accept_language,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": (
            f'"Not-A.Brand";v="99", "Chromium";v="{main_ver}", '
            f'"Google Chrome";v="{main_ver}"'
        ),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": f'"{oscpu}"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": f'"{chrome_ver}"',
        "sec-ch-ua-full-version-list": (
            f'"Not-A.Brand";v="99.0.0.0", '
            f'"Chromium";v="{chrome_ver}", '
            f'"Google Chrome";v="{chrome_ver}"'
        ),
        "sec-ch-ua-platform-version": '"10.0.0"',
        "DNT": "1",
        "Cache-Control": "max-age=0",
        "Referer": referer,
        "Priority": "u=0, i",
    }

    if site_origin:
        headers["Origin"] = site_origin

    return headers


def get_mobile_headers(site: str = "generic") -> dict[str, str]:
    """Return mobile browser headers for mobile-first pages / stricter bot checks."""
    # Rotate between several realistic mobile UAs
    mobile_uas = [
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; CPH2613) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    ]
    ua = random.choice(mobile_uas)

    accept_language = "en-US,en;q=0.9"
    referer = "https://www.google.com/"

    if site in {"croma", "flipkart", "tatacliq"}:
        accept_language = "en-IN,en;q=0.9,hi;q=0.8"
        referer = f"https://www.{site}.com/"
    elif site in {"walmart", "bestbuy", "target", "ebay"}:
        accept_language = "en-US,en;q=0.9"
        referer = "https://www.google.com/"

    return {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;"
            "q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": accept_language,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": '"Not-A.Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
        "Sec-CH-UA-Mobile": "?1",
        "Sec-CH-UA-Platform": '"Android"',
        "Referer": referer,
        "DNT": "1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua-arch": '"arm"',
        "sec-ch-ua-bitness": '"64"',
        "sec-ch-ua-full-version": '"124.0.6367.82"',
        "sec-ch-ua-full-version-list": (
            '"Not-A.Brand";v="99.0.0.0", '
            '"Chromium";v="124.0.6367.82", '
            '"Google Chrome";v="124.0.6367.82"'
        ),
        "sec-ch-ua-model": '"SM-S928B"',
        "sec-ch-ua-platform-version": '"14.0.0"',
        "Priority": "u=0, i",
    }
