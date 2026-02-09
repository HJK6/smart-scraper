"""
AI Web Scraper — Uses undetected Chrome + Claude to intelligently navigate websites.

On each page, the AI analyzes the content and decides the next action:
click a link, fill a form, extract data, scroll, or declare the task complete.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import logging
from typing import Optional

import anthropic
from bs4 import BeautifulSoup, Comment
from selenium.common.exceptions import (
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import ScraperAction, ScraperStep, ScrapeResult
from modules.driver_manager import DriverManager

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a web navigation AI. You are given a user's goal and the current page content.
Your job is to decide the next action to take in the browser to accomplish the goal.

You MUST respond with valid JSON only. No markdown, no explanation outside the JSON.

Available actions:
- {"action": "click", "selector": "<css_selector>", "reason": "why"}
- {"action": "type", "selector": "<css_selector>", "text": "text to type", "reason": "why"}
- {"action": "scroll_down", "reason": "why"}
- {"action": "scroll_up", "reason": "why"}
- {"action": "goto", "url": "<url>", "reason": "why"}
- {"action": "wait", "seconds": 2, "reason": "why"}
- {"action": "extract", "data": {<structured data you extracted>}, "reason": "why"}
- {"action": "done", "result": "<final answer or summary>", "data": {<optional structured data>}}
- {"action": "fail", "reason": "why the task cannot be completed"}

Guidelines:
- Use CSS selectors that are specific and robust (prefer IDs, data attributes, then classes).
- If you need to click a link, use the href or visible text to identify it.
- If the page seems empty or blocked, try scrolling or waiting.
- If you see a CAPTCHA or login wall you cannot bypass, report it with "fail".
- When you have gathered the requested information, use "extract" or "done".
- Be decisive. Pick one action per turn.
- If you're stuck in a loop, try a different approach or "fail".
"""


def clean_html_for_ai(html: str, max_length: int = 50000) -> str:
    """Strip noise from HTML, keep structure and text for AI analysis."""
    soup = BeautifulSoup(html, "lxml")

    # Remove script, style, noscript, svg, and comments
    for tag in soup(["script", "style", "noscript", "svg", "meta", "link"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Remove hidden elements
    for tag in soup.find_all(attrs={"style": re.compile(r"display\s*:\s*none")}):
        tag.decompose()
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()

    # Build a simplified representation
    lines = []
    # Title
    title = soup.find("title")
    if title:
        lines.append(f"PAGE TITLE: {title.get_text(strip=True)}")

    # Navigation links
    nav = soup.find("nav")
    if nav:
        nav_links = nav.find_all("a", href=True)
        if nav_links:
            lines.append("\nNAVIGATION:")
            for a in nav_links[:20]:
                lines.append(f"  [{a.get_text(strip=True)}] -> {a['href']}")

    # Forms
    forms = soup.find_all("form")
    for i, form in enumerate(forms):
        lines.append(f"\nFORM {i} (action={form.get('action', '?')}, method={form.get('method', 'get')}):")
        for inp in form.find_all(["input", "textarea", "select", "button"]):
            tag_type = inp.get("type", inp.name)
            name = inp.get("name", inp.get("id", ""))
            placeholder = inp.get("placeholder", "")
            value = inp.get("value", "")
            text = inp.get_text(strip=True)[:50] if inp.name in ("button", "select") else ""
            lines.append(f"  <{inp.name} type={tag_type} name={name} placeholder={placeholder} value={value}> {text}")

    # Links
    all_links = soup.find_all("a", href=True)
    if all_links:
        lines.append(f"\nLINKS ({len(all_links)} total, showing first 50):")
        seen = set()
        for a in all_links[:50]:
            href = a["href"]
            text = a.get_text(strip=True)[:80]
            key = (href, text)
            if key not in seen:
                seen.add(key)
                lines.append(f"  [{text}] -> {href}")

    # Buttons (non-form)
    buttons = soup.find_all("button")
    form_buttons = set()
    for form in forms:
        for btn in form.find_all("button"):
            form_buttons.add(id(btn))
    non_form_buttons = [b for b in buttons if id(b) not in form_buttons]
    if non_form_buttons:
        lines.append(f"\nBUTTONS:")
        for btn in non_form_buttons[:20]:
            btn_id = btn.get("id", "")
            btn_class = " ".join(btn.get("class", []))
            text = btn.get_text(strip=True)[:50]
            lines.append(f"  [{text}] id={btn_id} class={btn_class}")

    # Main text content
    main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.find("body")
    if main:
        text = main.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines.append(f"\nPAGE TEXT:\n{text}")

    output = "\n".join(lines)
    if len(output) > max_length:
        output = output[:max_length] + "\n... [TRUNCATED]"
    return output


class WebScraper:
    """
    AI-powered web scraper that uses Claude to navigate websites.

    Args:
        headless: Run Chrome without GUI (default: True)
        chrome_version_main: Major Chrome version (default: 144)
        max_steps: Maximum AI decision steps (default: 20)
        model: Anthropic model to use (default: claude-opus-4-20250514)
    """

    def __init__(
        self,
        headless: bool = True,
        chrome_version_main: int = 144,
        max_steps: int = 20,
        model: str = "claude-opus-4-20250514",
    ):
        self.headless = headless
        self.chrome_version_main = chrome_version_main
        self.max_steps = max_steps
        self.model = model
        self.client = anthropic.Anthropic()
        self.dm: Optional[DriverManager] = None
        self.steps: list[ScraperStep] = []

    def _init_browser(self):
        """Start undetected Chrome. Try headless first, fall back to headful."""
        if self.dm:
            return

        if self.headless:
            try:
                logger.info("Starting undetected Chrome (headless)...")
                self.dm = DriverManager(
                    undetected=True,
                    headless=True,
                    chrome_version_main=self.chrome_version_main,
                )
                self.dm.get("about:blank")
                logger.info("Headless Chrome started successfully.")
                return
            except Exception as e:
                logger.warning(f"Headless failed ({e}), falling back to headful...")
                try:
                    self.dm.close()
                except Exception:
                    pass
                self.dm = None

        logger.info("Starting undetected Chrome (headful)...")
        self.dm = DriverManager(
            undetected=True,
            headless=False,
            chrome_version_main=self.chrome_version_main,
        )

    def _get_page_context(self) -> str:
        """Get current page state for AI."""
        url = self.dm.get_current_url()
        html = self.dm.get_page_source()
        cleaned = clean_html_for_ai(html)
        return f"CURRENT URL: {url}\n\n{cleaned}"

    def _ask_ai(self, goal: str, page_context: str, history: list[ScraperStep]) -> ScraperAction:
        """Send page context to AI and get next action."""
        messages = []

        if history:
            history_text = "Previous actions this session:\n"
            for i, step in enumerate(history[-10:], 1):
                history_text += f"  {i}. {step.action} — {step.reason or ''}\n"
                if step.error:
                    history_text += f"     ERROR: {step.error}\n"
            messages.append({"role": "user", "content": history_text})
            messages.append({"role": "assistant", "content": "Understood. I'll take that history into account."})

        messages.append({
            "role": "user",
            "content": f"GOAL: {goal}\n\n{page_context}\n\nWhat is the next action?",
        })

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        text = response.content[0].text.strip()

        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            text = json_match.group(1)

        raw = None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if brace_match:
                try:
                    raw = json.loads(brace_match.group())
                except json.JSONDecodeError:
                    pass

        if raw is None:
            return ScraperAction(action="fail", reason=f"AI returned invalid JSON: {text[:200]}")

        return ScraperAction.from_dict(raw)

    def _execute_action(self, action: ScraperAction) -> str | None:
        """Execute a browser action. Returns error string or None on success."""
        try:
            if action.action == "click":
                elements = self.dm.driver.find_elements("css selector", action.selector)
                if not elements:
                    return f"No elements found for selector: {action.selector}"
                element = elements[0]
                self.dm.scroll_to_view(element)
                time.sleep(0.3)
                element.click()
                time.sleep(1)

            elif action.action == "type":
                elements = self.dm.driver.find_elements("css selector", action.selector)
                if not elements:
                    return f"No elements found for selector: {action.selector}"
                element = elements[0]
                self.dm.scroll_to_view(element)
                element.clear()
                element.send_keys(action.text)
                time.sleep(0.5)

            elif action.action == "scroll_down":
                self.dm.scroll_by(600)
                time.sleep(0.5)

            elif action.action == "scroll_up":
                self.dm.scroll_by(-600)
                time.sleep(0.5)

            elif action.action == "goto":
                self.dm.get(action.url)
                time.sleep(2)

            elif action.action == "wait":
                time.sleep(action.seconds or 2)

            elif action.action in ("extract", "done", "fail"):
                pass

            else:
                return f"Unknown action: {action.action}"

        except ElementClickInterceptedException as e:
            return f"Click intercepted: {e}"
        except StaleElementReferenceException:
            return "Element became stale — page may have changed"
        except NoSuchElementException as e:
            return f"Element not found: {e}"
        except Exception as e:
            return f"Action error: {e}"

        return None

    def scrape(self, goal: str, start_url: str) -> ScrapeResult:
        """
        Navigate the web to accomplish a goal.

        Args:
            goal: What to find/do (e.g. "Find the price of iPhone 17 Pro")
            start_url: Starting URL to navigate to

        Returns:
            ScrapeResult with success status, extracted data, and step history.
        """
        self._init_browser()
        self.steps: list[ScraperStep] = []

        def _make_step(step_num: int, action: ScraperAction, error: str | None = None) -> ScraperStep:
            return ScraperStep(
                step=step_num,
                url=self.dm.get_current_url(),
                action=action.action,
                selector=action.selector,
                text=action.text,
                data=action.data,
                result=action.result,
                reason=action.reason,
                error=error,
            )

        try:
            logger.info(f"Starting scrape: {goal}")
            logger.info(f"Navigating to: {start_url}")
            self.dm.get(start_url)
            time.sleep(2)

            for step_num in range(1, self.max_steps + 1):
                logger.info(f"Step {step_num}/{self.max_steps}")

                page_context = self._get_page_context()
                action = self._ask_ai(goal, page_context, self.steps)

                logger.info(f"  AI decided: {action.action} — {action.reason or ''}")

                if action.action == "done":
                    self.steps.append(_make_step(step_num, action))
                    return ScrapeResult(
                        success=True,
                        result=action.result,
                        data=action.data,
                        steps=self.steps,
                    )

                if action.action == "fail":
                    self.steps.append(_make_step(step_num, action, error=action.reason))
                    return ScrapeResult(
                        success=False,
                        error=action.reason,
                        steps=self.steps,
                    )

                if action.action == "extract":
                    self.steps.append(_make_step(step_num, action))
                    continue

                error = self._execute_action(action)
                self.steps.append(_make_step(step_num, action, error=error))

                if error:
                    logger.warning(f"  Action error: {error}")

            return ScrapeResult(
                success=False,
                error=f"Reached max steps ({self.max_steps}) without completing goal",
                steps=self.steps,
            )

        except Exception as e:
            logger.exception("Scrape failed with exception")
            return ScrapeResult(
                success=False,
                error=str(e),
                steps=self.steps,
            )

    def close(self):
        if self.dm:
            self.dm.close()
            self.dm = None


def run_scraper(goal: str, start_url: str, headless: bool = True, max_steps: int = 20) -> ScrapeResult:
    """Convenience function to run a scrape and clean up."""
    scraper = WebScraper(headless=headless, max_steps=max_steps)
    try:
        return scraper.scrape(goal, start_url)
    finally:
        scraper.close()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="AI Web Scraper")
    parser.add_argument("url", help="Starting URL")
    parser.add_argument("goal", help="What to find or accomplish")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--headful", action="store_true", help="Run with visible browser")
    args = parser.parse_args()

    result = run_scraper(
        goal=args.goal,
        start_url=args.url,
        headless=not args.headful,
        max_steps=args.max_steps,
    )

    print("\n" + "=" * 60)
    print(f"SUCCESS: {result.success}")
    if result.result:
        print(f"RESULT: {result.result}")
    if result.data:
        print(f"DATA: {json.dumps(result.data, indent=2)}")
    if result.error:
        print(f"ERROR: {result.error}")
    print(f"STEPS: {len(result.steps)}")
    for step in result.steps:
        err = f" [ERROR: {step.error}]" if step.error else ""
        print(f"  {step.step}. {step.action} — {step.reason or ''}{err}")
