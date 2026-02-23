"""
DriverManager — Selenium WebDriver wrapper with undetected Chrome support.

Provides a high-level API for browser automation: navigation, element interaction,
scrolling, screenshots, and network request interception.

Usage:
    # Undetected Chrome (bypasses bot detection)
    dm = DriverManager(undetected=True, headless=True)

    # Standard Chrome
    dm = DriverManager(undetected=False, headless=True)

    dm.get("https://example.com")
    element = dm.find_element_by_xpath("//h1")
    print(element.text)
    dm.close()
"""

from datetime import datetime
import json
import os
import time
import urllib
import urllib3
import certifi
import undetected_chromedriver as uc
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import Select


# Patch undetected_chromedriver to prevent __del__ errors
def patched_del(self):
    pass


uc.Chrome.__del__ = patched_del


def loadSoup(path):
    soup = BeautifulSoup(open(path), "html5lib")
    return soup


def saveImage(url, save_path):
    urllib.request.urlretrieve(url, save_path)


def getSoup(url):
    http = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())
    response = http.request("GET", url)
    soup = BeautifulSoup(response.data, "html5lib")
    return soup


def writeSoup(soup, out_path="./out.txt"):
    out = open(out_path, "w")
    for char in soup.prettify():
        try:
            out.write(char)
        except:
            continue


class DriverManager:
    """
    Selenium WebDriver wrapper with optional undetected Chrome support.

    Args:
        view: "desktop" or "mobile" (default: "desktop")
        headless: Run browser without GUI (default: True)
        undetected: Use undetected-chromedriver to bypass bot detection (default: True)
        chrome_version_main: Major Chrome version number (default: 144)
    """

    def __init__(self, **kwargs):
        self.view = kwargs.get("view", "desktop")
        self.headless = kwargs.get("headless", True)
        self.storage_path = "./out.txt"
        self.undetected = kwargs.get("undetected", True)
        self.chrome_version_main = kwargs.get("chrome_version_main", 144)
        self.refresh()

    def execute_script(self, script, *args):
        return self.driver.execute_script(script, *args)

    def get_current_url(self):
        return self.driver.current_url

    def tap(self, element):
        self.touch = webdriver.TouchActions(self.driver)
        self.touch.tap(element).perform()

    def switch_to_iframe(self, iframe):
        self.driver.switch_to.frame(iframe)

    def switch_to_main(self):
        self.driver.switch_to.default_content()

    def maximize(self):
        self.driver.maximize_window()

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            return

    def move_to_element(self, element):
        actions = ActionChains(self.driver)
        actions.move_to_element(element).perform()

    def next_sibling(self, element):
        return self.driver.execute_script(
            "return arguments[0].nextElementSibling", element
        )

    def nth_sibling(self, element, n):
        for _ in range(n):
            element = self.next_sibling(element)
        return element

    def wait_on_element_load(self, xpath, timeout=10):
        found = False
        start_time = datetime.now()
        while not found:
            if (datetime.now() - start_time).seconds > timeout:
                return
            try:
                self.driver.find_element(By.XPATH, xpath)
                found = True
            except NoSuchElementException:
                continue

    def wait_on_elements_load(self, xpaths, timeout=10):
        found = False
        start_time = datetime.now()
        while not found:
            if (datetime.now() - start_time).seconds > timeout:
                return
            for xpath in xpaths:
                try:
                    self.driver.find_element(By.XPATH, xpath)
                    found = True
                    break
                except NoSuchElementException:
                    continue

    def scroll(self, amount):
        self.driver.execute_script("window.scrollTo(0, {});".format(amount))

    def scroll_by(self, amount):
        self.driver.execute_script("window.scrollBy(0, {});".format(amount), "")

    def scroll_to_view(self, element):
        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior: 'auto', block: 'nearest'});",
            element,
        )

    def scroll_click(self, element):
        self.scroll_to_view(element)
        element.click()

    def execute_postback(self, target, argument):
        """
        Execute ASP.NET postback in a way that avoids strict mode restrictions.

        Args:
            target: The postback target (e.g., 'm_DisplayCore')
            argument: The postback argument (e.g., 'DisplayInternalAction|TabSwitch|98|PARCELMAP')
        """
        script = f"""
        setTimeout(function() {{
            try {{
                __doPostBack('{target}', '{argument}');
            }} catch(e) {{
                console.error('Postback error:', e);
            }}
        }}, 10);
        """
        self.driver.execute_script(script)

    def enable_network_logging(self):
        self.driver.execute_cdp_cmd("Network.enable", {})

    def get_network_traffic(self):
        """Get network request+response pairs with full details (status, headers, mimeType, resourceType)."""
        try:
            logs = self.driver.get_log("performance")
            requests = {}
            responses = {}

            for entry in logs:
                message = json.loads(entry["message"])["message"]
                method = message["method"]
                params = message.get("params", {})

                if method == "Network.requestWillBeSent":
                    req_id = params.get("requestId")
                    req = params.get("request", {})
                    requests[req_id] = {
                        "requestId": req_id,
                        "url": req.get("url", ""),
                        "method": req.get("method", ""),
                        "headers": req.get("headers", {}),
                        "postData": req.get("postData"),
                        "resourceType": params.get("type", ""),
                        "timestamp": entry["timestamp"],
                    }

                elif method == "Network.responseReceived":
                    req_id = params.get("requestId")
                    resp = params.get("response", {})
                    responses[req_id] = {
                        "status": resp.get("status"),
                        "mimeType": resp.get("mimeType", ""),
                        "headers": resp.get("headers", {}),
                    }

            # Merge requests with their responses
            traffic = []
            for req_id, req_data in requests.items():
                req_data["response"] = responses.get(req_id)
                traffic.append(req_data)

            return traffic
        except Exception:
            return []

    def get_response_body(self, request_id):
        """Get the response body for a specific request ID."""
        try:
            result = self.driver.execute_cdp_cmd(
                "Network.getResponseBody", {"requestId": request_id}
            )
            return result.get("body", "")
        except Exception:
            return None

    def get_browser_cookies(self):
        """Get all browser cookies as a dict (name -> value)."""
        try:
            cookies = self.driver.get_cookies()
            return {c["name"]: c["value"] for c in cookies}
        except Exception:
            return {}

    def get_network_requests(self, only_xhr=False):
        try:
            logs = self.driver.get_log("performance")
            requests = []

            for entry in logs:
                message = json.loads(entry["message"])["message"]
                if message["method"] == "Network.requestWillBeSent":
                    req = message["params"]["request"]

                    if only_xhr:
                        accept_header = req.get("headers", {}).get("Accept", "")
                        if (
                            "application/json" not in accept_header
                            and "text/html" not in accept_header
                        ):
                            continue

                    requests.append(
                        {
                            "url": req["url"],
                            "method": req["method"],
                            "headers": req.get("headers"),
                            "postData": req.get("postData"),
                            "timestamp": entry["timestamp"],
                        }
                    )

            return requests
        except Exception:
            return []

    def get_network_requests_by_url(self, url_partial, only_xhr=False):
        try:
            all_requests = self.get_network_requests(only_xhr=only_xhr)
            return [r for r in all_requests if url_partial.lower() in r["url"].lower()]
        except Exception:
            return []

    def get_network_requests_by_method(self, method, only_xhr=False):
        try:
            all_requests = self.get_network_requests(only_xhr=only_xhr)
            return [r for r in all_requests if r["method"].upper() == method.upper()]
        except Exception:
            return []

    def get_network_requests_by_url_and_method(self, url_partial, method, only_xhr=False):
        try:
            all_requests = self.get_network_requests(only_xhr=only_xhr)
            return [
                r for r in all_requests
                if url_partial.lower() in r["url"].lower()
                and r["method"].upper() == method.upper()
            ]
        except Exception:
            return []

    def clear_network_logs(self):
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

    def start_undetected_chromedriver(self):
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")

        if self.headless:
            options.add_argument("--headless=new")
            # Override UA to remove "HeadlessChrome" — Cloudflare rejects it otherwise
            options.add_argument(
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            )

        # Enable performance log so get_log("performance") / network requests work
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # Match installed Chrome major version to avoid SessionNotCreatedException
        self.driver = uc.Chrome(options=options, version_main=self.chrome_version_main)

    def refresh(self):
        self.close()

        if self.undetected:
            self.start_undetected_chromedriver()
            return

        options = webdriver.ChromeOptions()
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-web-security")

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/92.0.4515.159 Safari/537.36"
        )
        options.add_argument(f"user-agent={user_agent}")
        options.add_argument("--disable-gpu")
        options.add_argument("enable-automation")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-browser-side-navigation")
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        if self.headless:
            options.add_argument("--headless")
            options.add_argument("--autoplay-policy=no-user-gesture-required")
            options.add_experimental_option("excludeSwitches", ["--mute-audio"])

        if self.view == "mobile":
            mobile_emulation = {"deviceName": "iPhone X"}
            options.add_experimental_option("mobileEmulation", mobile_emulation)
            options.add_argument("--disable-notifications")
        elif self.view == "desktop":
            options.add_argument("--start-maximized")
            options.add_argument("--disable-notifications")
            options.add_experimental_option(
                "excludeSwitches",
                [
                    "ignore-certificate-errors",
                    "safebrowsing-disable-download-protection",
                    "safebrowsing-disable-auto-update",
                    "disable-client-side-phishing-detection",
                ],
            )

        self.driver = webdriver.Chrome(options=options)

    def set_view(self, view):
        if self.view != view:
            self.view = view
            self.refresh()

    def set_storage_path(self, path):
        self.storage_path = path

    def screenshot(self, file, width=0, height=0):
        if not width or not height:
            metrics = self.driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
            if not width:
                width = metrics["contentSize"]["width"]
            if not height:
                height = metrics["contentSize"]["height"]

        self.driver.set_window_size(width, height)
        self.driver.save_screenshot(file)

    def get(self, url):
        refreshes = 0
        while refreshes < 4:
            try:
                self.driver.get(url)
                break
            except Exception:
                self.refresh()
                refreshes += 1

    def get_soup(self):
        body = self.driver.find_element(By.TAG_NAME, "body")
        return BeautifulSoup(body.get_attribute("outerHTML"), "lxml")

    def get_element_html(self, element):
        return element.get_attribute("outerHTML")

    def get_page_source(self):
        return self.driver.execute_script("return document.documentElement.outerHTML;")

    def store_soup(self):
        with open("./soup.txt", "w") as out:
            soup = BeautifulSoup(
                self.driver.find_element(By.TAG_NAME, "body").get_attribute("outerHTML"),
                "lxml",
            )
            for char in soup.prettify():
                try:
                    out.write(char)
                except Exception:
                    pass

    def load_soup(self):
        soup = BeautifulSoup(open("./soup.txt"), "lxml")
        return soup

    def implicitly_wait(self, wait_time):
        self.driver.implicitly_wait(wait_time)

    def find_element_by_xpath(self, xpath):
        return self.driver.find_element(By.XPATH, xpath)

    def find_element_by_id(self, id_):
        return self.driver.find_element(By.ID, id_)

    def find_elements_by_xpath(self, xpath):
        return self.driver.find_elements(By.XPATH, xpath)

    def find_element_by_tag_name(self, tag_name):
        return self.driver.find_element(By.TAG_NAME, tag_name)

    def find_elements_by_tag_name(self, tag_name):
        return self.driver.find_elements(By.TAG_NAME, tag_name)

    def find_elements_by_class_name(self, class_name):
        return self.driver.find_elements(By.CLASS_NAME, class_name)

    def find_element_by_class_name(self, class_name):
        return self.driver.find_element(By.CLASS_NAME, class_name)

    def find_element_by_link_text(self, link_text):
        return self.driver.find_element(By.LINK_TEXT, link_text)

    def find_element_by_name(self, name):
        return self.driver.find_element(By.NAME, name)

    def select_by_value(self, element_id: str, value: str) -> None:
        """Find a select by id and select the option with the given value."""
        el = self.driver.find_element(By.ID, element_id)
        Select(el).select_by_value(value)


def explore_page(url: str, output_dir: str = "debug/web-manager-explorer") -> None:
    """
    Open undetected Chrome, enable network logging, navigate to url,
    then save all network requests and page HTML under output_dir for analysis.
    """
    dm = DriverManager(undetected=True)
    try:
        dm.enable_network_logging()
        dm.get(url)
        time.sleep(3)
        requests = dm.get_network_requests()
        html = dm.get_page_source()
    finally:
        dm.close()

    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    requests_path = os.path.join(output_dir, f"network_requests_{ts}.json")
    html_path = os.path.join(output_dir, f"page_{ts}.html")
    with open(requests_path, "w", encoding="utf-8") as f:
        json.dump(requests, f, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved {len(requests)} requests to {requests_path}")
    print(f"Saved HTML to {html_path}")
