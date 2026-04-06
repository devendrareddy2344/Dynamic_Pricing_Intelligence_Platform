from scraping_service.scrapers.amazon import scrape_amazon
from scraping_service.scrapers.bestbuy import scrape_bestbuy
from scraping_service.scrapers.croma import scrape_croma
from scraping_service.scrapers.flipkart import scrape_flipkart
from scraping_service.scrapers.target import scrape_target
from scraping_service.scrapers.walmart import scrape_walmart
from scraping_service.scrapers.tatacliq import scrape_tatacliq
from scraping_service.scrapers.ebay import scrape_ebay

SCRAPER_FUNCS = {
    "amazon": scrape_amazon,
    "walmart": scrape_walmart,
    "bestbuy": scrape_bestbuy,
    "flipkart": scrape_flipkart,
    "croma": scrape_croma,
    "target": scrape_target,
    "tatacliq": scrape_tatacliq,
    "ebay": scrape_ebay,
}
