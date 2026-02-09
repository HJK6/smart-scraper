# Smart Scraper

AI-powered web scraping toolkit using Claude and undetected Chrome. Tell it what you want in plain English and it navigates websites autonomously to find it.

## Features

- **AI-Guided Scraping** — Claude analyzes each page and decides the next browser action (click, type, scroll, extract)
- **Self-Learning Recipes** — SmartCrawler records successful scrapes and generates deterministic recipes for instant replay
- **Bot-Detection Bypass** — Uses `undetected-chromedriver` to avoid being blocked
- **Headless or Headful** — Run invisible or watch the browser work

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

Requires **Google Chrome** installed on the system.

## Usage

### CLI

```bash
# One-shot scrape
python modules/web_scraper.py "https://www.apple.com" "Find the price of the iPhone 17 Pro"

# Smart crawl (learns and replays)
python modules/smart_crawler.py "https://books.toscrape.com" "Find the title and price of the first book"

# Visible browser for debugging
python modules/smart_crawler.py "https://example.com" "Find contact info" --headful
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
```

### DriverManager (standalone browser automation)

```python
from modules.driver_manager import DriverManager

dm = DriverManager(undetected=True, headless=True)
dm.get("https://example.com")
print(dm.get_page_source())
dm.close()
```

## How SmartCrawler Works

1. **First run** — AI navigates the site step-by-step, finds your data
2. **Generates recipe** — Converts the successful run into a deterministic script
3. **Next run** — Replays the recipe instantly (no AI calls, no cost)
4. **Auto-fallback** — If the recipe breaks (site changed), falls back to AI and regenerates

## Documentation

See [CLAUDE.md](CLAUDE.md) for full API documentation, all DriverManager methods, data models, and troubleshooting.
