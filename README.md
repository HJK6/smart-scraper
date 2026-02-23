# Smart Scraper

AI-powered web scraping toolkit using Claude and undetected Chrome. Tell it what you want in plain English and it navigates websites autonomously to find it.

## Features

- **AI-Guided Scraping** — Claude analyzes each page and decides the next browser action (click, type, scroll, extract)
- **Self-Learning Recipes** — SmartCrawler records successful scrapes and generates deterministic recipes for instant replay
- **Bot-Detection Bypass** — Uses `undetected-chromedriver` with headless UA spoofing to avoid Cloudflare and other bot detectors
- **API Discovery** — Automatically monitors network traffic during scrapes to find hidden API endpoints, tests them for auth requirements
- **Headless or Headful** — Run invisible or watch the browser work

## Setup

```bash
pip install -r requirements.txt
```

Requires **Google Chrome** installed and the **Claude CLI** (`claude`) in PATH. Uses your Claude Max subscription — no API key needed.

## Usage

### CLI

```bash
# One-shot scrape
python modules/web_scraper.py "https://www.apple.com" "Find the price of the iPhone 17 Pro"

# Smart crawl (learns and replays)
python modules/smart_crawler.py "https://books.toscrape.com" "Find the title and price of the first book"

# Visible browser for debugging
python modules/smart_crawler.py "https://example.com" "Find contact info" --headful

# Manage recipes
python modules/smart_crawler.py "" "" --list-recipes
python modules/smart_crawler.py "" "" --delete-recipe abc123def456
```

### Python

```python
from modules.smart_crawler import smart_crawl

result = smart_crawl(
    goal="Find the price of the iPhone 17 Pro",
    start_url="https://www.apple.com",
)

if result.success:
    print(result.result)  # "Starts at $1099"
    print(result.data)    # {"product": "iPhone 17 Pro", "price": "$1099"}

# Check discovered APIs
for api in result.discovered_apis:
    print(api.url, api.works_without_auth)
```

### DriverManager (standalone browser automation)

```python
from modules.driver_manager import DriverManager

dm = DriverManager(undetected=True, headless=True)
dm.get("https://example.com")
print(dm.get_page_source())
dm.close()
```

### Network Traffic & API Discovery

```python
from modules.driver_manager import DriverManager

dm = DriverManager(undetected=True, headless=True)
dm.enable_network_logging()
dm.get("https://example.com")

# Get all network request+response pairs
traffic = dm.get_network_traffic()

# Get response body for a specific request
body = dm.get_response_body(traffic[0]["requestId"])

# Get browser cookies
cookies = dm.get_browser_cookies()

# Filter requests
xhr_requests = dm.get_network_requests(only_xhr=True)
api_requests = dm.get_network_requests_by_url("api.example.com")

dm.close()
```

### Quick Page Exploration

```python
from modules.driver_manager import explore_page

# Saves network requests + HTML to debug/web-manager-explorer/
explore_page("https://example.com")
```

## How SmartCrawler Works

1. **First run** — AI navigates the site step-by-step, finds your data
2. **Generates recipe** — Converts the successful run into a deterministic script
3. **Next run** — Replays the recipe instantly (no AI calls, no cost)
4. **Auto-fallback** — If the recipe breaks (site changed), falls back to AI and regenerates
5. **API discovery** — After each scrape, analyzes network traffic to find usable API endpoints

## Architecture

```
smart-scraper/
├── models.py                    # DataclassBase + all data models
├── modules/
│   ├── driver_manager.py        # DriverManager — Selenium wrapper with undetected Chrome
│   ├── web_scraper.py           # WebScraper — AI-guided single-run scraper
│   └── smart_crawler.py         # SmartCrawler — self-learning crawler with recipe system
├── recipes/                     # Auto-generated deterministic recipes (JSON)
├── requirements.txt             # Python dependencies
├── CLAUDE.md                    # Full API documentation
└── README.md
```

## Documentation

See [CLAUDE.md](CLAUDE.md) for full API documentation, all DriverManager methods, data models, and troubleshooting.
