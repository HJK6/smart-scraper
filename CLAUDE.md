# Smart Scraper — AI-Powered Web Scraping Toolkit

This package provides three layers of web scraping powered by Claude AI and undetected Chrome.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Requires `claude` CLI in PATH (uses Max subscription, no API key needed)

# Run a scrape
python modules/smart_crawler.py "https://example.com" "Find the main heading"
```

## Prerequisites

1. **Python 3.9+**
2. **Google Chrome** installed on the system
3. **Claude CLI** — `claude` must be in PATH (uses Max subscription, no API key needed)
4. **Python packages** — install via `pip install -r requirements.txt`:
   - `selenium` — browser automation
   - `undetected-chromedriver` — bot-detection bypass for Chrome
   - `beautifulsoup4` + `lxml` + `html5lib` — HTML parsing
   - `certifi` + `urllib3` — HTTP utilities
   - `requests` — API endpoint testing

### macOS ARM64 (Apple Silicon) Notes

If `undetected-chromedriver` gets killed by Gatekeeper (SIGKILL), you may need to:
1. Patch `patcher.py` to detect `mac-arm64` platform in `_set_platform_name()`
2. Run `codesign --force --sign - <path-to-chromedriver>` after patching

### Chrome Version

The default `chrome_version_main` is **144**. If your Chrome version differs, pass it explicitly:
```python
dm = DriverManager(undetected=True, headless=True, chrome_version_main=130)
```

Check your Chrome version: Chrome menu > About Google Chrome.

---

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
└── CLAUDE.md                    # This file
```

---

## Components

### 1. DriverManager (`modules/driver_manager.py`)

Selenium WebDriver wrapper with undetected Chrome support. **Always use this for browser automation.**

```python
from modules.driver_manager import DriverManager

# Undetected Chrome (bypasses bot detection) — RECOMMENDED
dm = DriverManager(undetected=True, headless=True)

# With visible browser window (for debugging)
dm = DriverManager(undetected=True, headless=False)

# Standard Chrome (no bot-detection bypass)
dm = DriverManager(undetected=False, headless=True)
```

**Key methods:**
| Method | Description |
|--------|-------------|
| `dm.get(url)` | Navigate to URL (auto-retries up to 4x) |
| `dm.get_current_url()` | Get current page URL |
| `dm.get_page_source()` | Get full page HTML |
| `dm.get_soup()` | Get page as BeautifulSoup object |
| `dm.find_element_by_xpath(xpath)` | Find element by XPath |
| `dm.find_element_by_id(id)` | Find element by ID |
| `dm.find_elements_by_xpath(xpath)` | Find multiple elements |
| `dm.scroll_to_view(element)` | Scroll element into viewport |
| `dm.scroll_click(element)` | Scroll to element and click |
| `dm.scroll_by(pixels)` | Scroll page by N pixels |
| `dm.screenshot(filepath)` | Take full-page screenshot |
| `dm.wait_on_element_load(xpath, timeout)` | Wait for element to appear |
| `dm.switch_to_iframe(iframe)` | Switch to iframe context |
| `dm.switch_to_main()` | Switch back to main document |
| `dm.execute_postback(target, argument)` | Execute ASP.NET postback |
| `dm.select_by_value(element_id, value)` | Select dropdown option by value |
| `dm.close()` | Quit the browser |

**Network logging methods:**
| Method | Description |
|--------|-------------|
| `dm.enable_network_logging()` | Start capturing network requests |
| `dm.get_network_requests()` | Get captured network requests |
| `dm.get_network_requests(only_xhr=True)` | Get only XHR/Fetch requests |
| `dm.get_network_requests_by_url(partial)` | Filter requests by URL substring |
| `dm.get_network_requests_by_method(method)` | Filter requests by HTTP method |
| `dm.get_network_requests_by_url_and_method(url, method)` | Filter by both |
| `dm.get_network_traffic()` | Get full request+response pairs (status, headers, mimeType) |
| `dm.get_response_body(requestId)` | Get response body for a specific request |
| `dm.get_browser_cookies()` | Get all browser cookies as dict |
| `dm.clear_network_logs()` | Clear captured logs |

**Quick page exploration:**
```python
from modules.driver_manager import explore_page

explore_page("https://example.com")
# Saves network requests + HTML to debug/web-manager-explorer/
```

**Important:** Always use `undetected=True` when scraping real websites. Standard Chrome gets detected and blocked by most sites. In headless mode, the UA string is overridden to remove "HeadlessChrome" which Cloudflare would otherwise reject.

---

### 2. WebScraper (`modules/web_scraper.py`)

AI-powered single-run scraper. Claude AI analyzes each page and decides what to do next. Automatically discovers API endpoints from network traffic.

```python
from modules.web_scraper import run_scraper

result = run_scraper(
    goal="Find the price of the iPhone 17 Pro",
    start_url="https://www.apple.com",
    headless=True,
    max_steps=20,
)

if result.success:
    print(result.result)  # "The iPhone 17 Pro starts at $1099"
    print(result.data)    # {"product": "iPhone 17 Pro", "price": "$1099"}

# Check discovered API endpoints
for api in result.discovered_apis:
    print(f"{api.method} {api.url} — auth: {api.works_without_auth}")
```

**CLI:**
```bash
python modules/web_scraper.py "https://www.apple.com" "Find the price of the iPhone 17 Pro"
python modules/web_scraper.py "https://example.com" "Find contact info" --headful
python modules/web_scraper.py "https://example.com" "Find pricing" --max-steps 30
```

**How it works:**
1. Opens Chrome and navigates to `start_url`
2. Enables network logging to capture API traffic
3. Cleans HTML and sends page context to Claude
4. Claude returns a JSON action: click, type, scroll, goto, extract, done, or fail
5. Executes the action, then repeats until `done` or max steps reached
6. Analyzes network traffic to discover API endpoints

**Supported AI actions:**
| Action | Description |
|--------|-------------|
| `click` | Click element by CSS selector |
| `type` | Type text into an input field |
| `scroll_down` / `scroll_up` | Scroll the page |
| `goto` | Navigate to a URL |
| `wait` | Wait N seconds |
| `extract` | Extract structured data from the page |
| `done` | Task complete — return result |
| `fail` | Task cannot be completed |

---

### 3. SmartCrawler (`modules/smart_crawler.py`)

Self-learning crawler that generates reusable recipes. **Use this for repeated scraping tasks.**

```python
from modules.smart_crawler import smart_crawl

# First run: AI-guided → generates a recipe
result = smart_crawl(
    goal="Find the title and price of the first book",
    start_url="https://books.toscrape.com",
)
# result.recipe_generated = True
# result.recipe_id = "abc123..."

# Second run: Uses saved recipe (no AI calls, fast!)
result = smart_crawl(
    goal="Find the title and price of the first book",
    start_url="https://books.toscrape.com",
)
# result.used_recipe = True
```

**CLI:**
```bash
# Smart crawl (recipe if exists, AI otherwise)
python modules/smart_crawler.py "https://example.com" "Find the main heading"

# Force AI (skip existing recipe)
python modules/smart_crawler.py "https://example.com" "Find pricing" --force-ai

# List all saved recipes
python modules/smart_crawler.py "" "" --list-recipes

# Delete a recipe
python modules/smart_crawler.py "" "" --delete-recipe abc123def456
```

**Recipe system:**
- Recipes saved as JSON in `recipes/` directory
- Keyed by `sha256(domain + goal)` — same goal on same domain reuses the recipe
- Steps support `{variable}` placeholders for dynamic values
- Each step has `fallback_selectors` for resilience
- If a recipe fails mid-run, automatically falls back to AI
- Tracks success rate and fallback count

**Variables for dynamic recipes:**
```python
result = smart_crawl(
    goal="Search for a product",
    start_url="https://example.com",
    variables={"search_term": "laptop", "max_price": "1000"},
)
```

---

## API Discovery

Both WebScraper and SmartCrawler automatically monitor network traffic during scrapes. After each scrape, they:
1. Filter for XHR/Fetch/JSON API responses
2. Attempt to call each endpoint without auth (plain GET)
3. If that fails, retry with browser cookies
4. Return `discovered_apis` in the result (list of `DiscoveredApi` objects)

```python
result = smart_crawl(goal="...", start_url="...")
for api in result.discovered_apis:
    print(api.url, api.method, api.works_without_auth, api.works_with_cookies)
    print(api.response_preview)  # first 2000 chars of response
```

---

## Data Models (`models.py`)

All models extend `DataclassBase` which provides `from_dict()`, `to_dict()`, `from_json()`, `to_json()`.

| Model | Purpose |
|-------|---------|
| `ScraperAction` | AI decision: action, selector, text, url, data, result, reason |
| `ScraperStep` | Recorded step: step#, url, action, selector, error |
| `ScrapeResult` | WebScraper result: success, result, data, steps, error, discovered_apis |
| `DiscoveredApi` | API endpoint: url, method, auth status, response preview |
| `RecipeStep` | Single recipe step with fallback selectors |
| `CrawlerRecipe` | Full recipe: id, domain, goal, steps, stats |
| `SmartCrawlResult` | SmartCrawler result: extends ScrapeResult + recipe info + discovered_apis |

---

## WebScraper vs SmartCrawler

| Feature | WebScraper | SmartCrawler |
|---------|-----------|--------------|
| AI calls | Every step | Only first run (or fallback) |
| Speed | Slower | Fast after first run |
| Learning | No | Yes (generates recipes) |
| Cost | Higher | Much lower after first run |
| API discovery | Yes | Yes |
| Use case | One-off scrapes | Repeated scrapes |

**Recommendation:** Use `SmartCrawler` by default. It gives you the best of both worlds.
