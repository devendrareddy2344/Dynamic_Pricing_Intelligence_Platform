import httpx
import sys
import os
# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_service.scrapers.headers import get_mobile_headers, get_browser_headers
from scraping_service.user_agents import random_ua

for site in ['flipkart', 'walmart', 'croma', 'bestbuy']:
    if site == 'flipkart':
        url = 'https://www.flipkart.com/search?q=Samsung+Galaxy+S25+Ultra'
        headers = get_mobile_headers(); headers['User-Agent']=random_ua(); headers['Referer']='https://www.google.com/'
    elif site == 'walmart':
        url = 'https://www.walmart.com/search?q=Samsung+Galaxy+S25+Ultra'
        headers = get_mobile_headers(); headers['User-Agent']=random_ua(); headers['Referer']='https://www.google.com/'
    elif site == 'croma':
        url = 'https://www.croma.com/searchB?q=Samsung+Galaxy+S25+Ultra'
        headers = get_browser_headers('croma'); headers['User-Agent']=random_ua()
    else:
        url = 'https://www.bestbuy.com/site/searchpage.jsp?st=Samsung+Galaxy+S25+Ultra'
        headers = get_browser_headers('bestbuy'); headers['User-Agent']=random_ua(); headers['Referer']='https://www.google.com/'

    print('\n===', site, '===')
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            print('status', r.status_code, 'url', r.url)
            print('blocked?', 'blocked' in r.text.lower() or 'captcha' in r.text.lower())
            print('len', len(r.text))
    except Exception as e:
        print('ERR', type(e).__name__, e)
