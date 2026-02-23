"""
Smart Crawler — Self-learning web crawler that builds deterministic recipes from AI-guided sessions.

Flow:
1. Check if a recipe exists for the (domain, goal) pair
2. If YES → Execute recipe deterministically. On failure, fall back to AI.
3. If NO → Run AI-guided crawl (like web_scraper.py), record every step,
   then generate a deterministic recipe from the successful run.

Recipes are saved as JSON in the recipes/ directory and can be reused
for identical or similar goals on the same domain.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models import (
    CrawlerRecipe,
    DiscoveredApi,
    RecipeStep,
    ScraperAction,
    ScraperStep,
    SmartCrawlResult,
)
from modules.web_scraper import WebScraper, clean_html_for_ai, call_claude_cli, analyze_network_for_apis
from modules.driver_manager import DriverManager

logger = logging.getLogger(__name__)

RECIPES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recipes")

RECIPE_GEN_PROMPT = """\
You are a recipe generator. Given a successful web crawl session (goal + steps taken),
generate a deterministic recipe that can replicate the same crawl WITHOUT AI.

The recipe must be a JSON object with this structure:
{
  "steps": [
    {
      "action": "goto|click|type|scroll_down|scroll_up|wait|extract",
      "selector": "css selector (for click/type/extract)",
      "text": "text to type (for type action, use {variable} for dynamic values)",
      "url": "url (for goto, use {variable} for dynamic parts)",
      "seconds": 2,
      "extract_fields": {"field_name": "css_selector"},
      "description": "human-readable description",
      "fallback_selectors": ["alt_selector1", "alt_selector2"],
      "optional": false,
      "wait_after": 1.0
    }
  ],
  "extract_fields": {"field_name": "css_selector"}
}

Rules:
- Use robust CSS selectors (prefer IDs > data attributes > classes > tag+text combos)
- Include fallback_selectors where possible (2-3 alternatives)
- Skip scroll/wait steps that were just exploratory — only keep essential ones
- For type actions with dynamic values, use {variable} placeholders
- For extraction, map field names to CSS selectors that locate the data
- Mark truly optional steps with "optional": true
- Add descriptive labels for each step
- Combine consecutive identical scrolls into a single step if possible
- If a step was retried due to error, use the selector that worked

Respond with ONLY the JSON recipe, no markdown or explanation.
"""


class SmartCrawler:
    """Self-learning crawler: AI-guided first run, deterministic recipe thereafter."""

    def __init__(
        self,
        headless: bool = True,
        chrome_version_main: int = 144,
        max_steps: int = 25,
    ):
        self.headless = headless
        self.chrome_version_main = chrome_version_main
        self.max_steps = max_steps
        self.dm: Optional[DriverManager] = None
        self.steps: list[ScraperStep] = []

    # ------------------------------------------------------------------
    # Recipe management
    # ------------------------------------------------------------------

    @staticmethod
    def _recipe_id(domain: str, goal: str) -> str:
        """Generate a stable recipe ID from domain + goal."""
        key = f"{domain}::{goal.lower().strip()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @staticmethod
    def _recipe_path(recipe_id: str) -> str:
        return os.path.join(RECIPES_DIR, f"{recipe_id}.json")

    def _load_recipe(self, domain: str, goal: str) -> Optional[CrawlerRecipe]:
        """Load an existing recipe for this domain+goal if one exists."""
        rid = self._recipe_id(domain, goal)
        path = self._recipe_path(rid)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            recipe = CrawlerRecipe.from_dict(data)
            logger.info(f"Loaded recipe {rid} (used {recipe.times_used}x, "
                        f"success rate {recipe.times_succeeded}/{recipe.times_used})")
            return recipe
        except Exception as e:
            logger.warning(f"Failed to load recipe {rid}: {e}")
            return None

    def _save_recipe(self, recipe: CrawlerRecipe):
        """Save a recipe to disk."""
        os.makedirs(RECIPES_DIR, exist_ok=True)
        path = self._recipe_path(recipe.recipe_id)
        with open(path, "w") as f:
            json.dump(recipe.to_dict(), f, indent=2)
        logger.info(f"Saved recipe {recipe.recipe_id} to {path}")

    # ------------------------------------------------------------------
    # Browser management
    # ------------------------------------------------------------------

    def _init_browser(self):
        if self.dm:
            return
        if self.headless:
            try:
                logger.info("Starting undetected Chrome (headless)...")
                self.dm = DriverManager(
                    undetected=True, headless=True,
                    chrome_version_main=self.chrome_version_main,
                )
                self.dm.get("about:blank")
                self.dm.enable_network_logging()
                logger.info("Headless Chrome started.")
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
            undetected=True, headless=False,
            chrome_version_main=self.chrome_version_main,
        )
        self.dm.enable_network_logging()

    def close(self):
        if self.dm:
            self.dm.close()
            self.dm = None

    def _discover_apis(self) -> list:
        """Run network traffic analysis to find API endpoints."""
        try:
            return analyze_network_for_apis(self.dm)
        except Exception as e:
            logger.warning(f"API discovery failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Deterministic recipe execution
    # ------------------------------------------------------------------

    def _execute_recipe_step(self, step: RecipeStep, variables: Dict[str, str] = None) -> Optional[str]:
        """Execute a single recipe step. Returns error string or None."""
        variables = variables or {}

        try:
            if step.action == "goto":
                url = step.url or ""
                for k, v in variables.items():
                    url = url.replace(f"{{{k}}}", v)
                self.dm.get(url)
                time.sleep(step.wait_after)

            elif step.action == "click":
                element = self._find_with_fallbacks(step.selector, step.fallback_selectors)
                if not element:
                    return f"No element found for selector: {step.selector} (+ {len(step.fallback_selectors)} fallbacks)"
                self.dm.scroll_to_view(element)
                time.sleep(0.3)
                element.click()
                time.sleep(step.wait_after)

            elif step.action == "type":
                element = self._find_with_fallbacks(step.selector, step.fallback_selectors)
                if not element:
                    return f"No element found for selector: {step.selector}"
                text = step.text or ""
                for k, v in variables.items():
                    text = text.replace(f"{{{k}}}", v)
                self.dm.scroll_to_view(element)
                element.clear()
                element.send_keys(text)
                time.sleep(step.wait_after)

            elif step.action == "scroll_down":
                self.dm.scroll_by(600)
                time.sleep(step.wait_after)

            elif step.action == "scroll_up":
                self.dm.scroll_by(-600)
                time.sleep(step.wait_after)

            elif step.action == "wait":
                time.sleep(step.seconds or 2)

            elif step.action == "extract":
                pass  # Handled by caller using extract_fields

            else:
                return f"Unknown action: {step.action}"

        except ElementClickInterceptedException as e:
            return f"Click intercepted: {e}"
        except StaleElementReferenceException:
            return "Element became stale"
        except NoSuchElementException as e:
            return f"Element not found: {e}"
        except Exception as e:
            return f"Action error: {e}"

        return None

    def _find_with_fallbacks(self, primary: str, fallbacks: List[str] = None):
        """Try primary selector, then fallbacks. Returns element or None."""
        selectors = [primary] + (fallbacks or [])
        for sel in selectors:
            if not sel:
                continue
            try:
                elements = self.dm.driver.find_elements("css selector", sel)
                if elements:
                    return elements[0]
            except Exception:
                continue
        return None

    def _extract_data(self, extract_fields: Dict[str, str]) -> Dict[str, Any]:
        """Extract data from the current page using CSS selectors."""
        data = {}
        for field_name, selector in extract_fields.items():
            try:
                elements = self.dm.driver.find_elements("css selector", selector)
                if elements:
                    if len(elements) == 1:
                        data[field_name] = elements[0].text.strip()
                    else:
                        data[field_name] = [el.text.strip() for el in elements]
                else:
                    data[field_name] = None
            except Exception as e:
                data[field_name] = None
                logger.warning(f"Extract field '{field_name}' failed: {e}")
        return data

    def _run_recipe(self, recipe: CrawlerRecipe, variables: Dict[str, str] = None) -> SmartCrawlResult:
        """Execute a recipe deterministically. Returns result."""
        logger.info(f"Running recipe {recipe.recipe_id} ({len(recipe.steps)} steps)")
        self.steps = []
        variables = variables or {}

        self._init_browser()

        try:
            for i, step in enumerate(recipe.steps, 1):
                logger.info(f"  Recipe step {i}/{len(recipe.steps)}: {step.action} — {step.description}")

                error = self._execute_recipe_step(step, variables)

                self.steps.append(ScraperStep(
                    step=i,
                    url=self.dm.get_current_url(),
                    action=step.action,
                    selector=step.selector,
                    text=step.text,
                    reason=step.description,
                    error=error,
                ))

                if error:
                    if step.optional:
                        logger.warning(f"  Optional step failed (continuing): {error}")
                        continue
                    else:
                        logger.error(f"  Recipe step failed: {error}")
                        return SmartCrawlResult(
                            success=False,
                            error=f"Recipe step {i} failed: {error}",
                            steps=self.steps,
                            used_recipe=True,
                            recipe_id=recipe.recipe_id,
                        )

            # Final extraction
            data = {}
            if recipe.extract_fields:
                data = self._extract_data(recipe.extract_fields)

            # Also check last step for extract_fields
            for step in recipe.steps:
                if step.extract_fields:
                    data.update(self._extract_data(step.extract_fields))

            # Update recipe stats
            recipe.times_used += 1
            recipe.times_succeeded += 1
            recipe.last_used = int(time.time())
            self._save_recipe(recipe)

            apis = self._discover_apis()
            return SmartCrawlResult(
                success=True,
                data=data if data else None,
                result=f"Recipe executed successfully ({len(recipe.steps)} steps)",
                steps=self.steps,
                used_recipe=True,
                recipe_id=recipe.recipe_id,
                discovered_apis=apis,
            )

        except Exception as e:
            logger.exception("Recipe execution failed")
            apis = self._discover_apis()
            return SmartCrawlResult(
                success=False,
                error=str(e),
                steps=self.steps,
                used_recipe=True,
                recipe_id=recipe.recipe_id,
                discovered_apis=apis,
            )

    # ------------------------------------------------------------------
    # AI-guided crawl (same as web_scraper but records for recipe gen)
    # ------------------------------------------------------------------

    def _get_page_context(self) -> str:
        url = self.dm.get_current_url()
        html = self.dm.get_page_source()
        cleaned = clean_html_for_ai(html)
        return f"CURRENT URL: {url}\n\n{cleaned}"

    def _ask_ai(self, goal: str, page_context: str, history: list[ScraperStep]) -> ScraperAction:
        """Send page context to Claude CLI and get next action."""
        from modules.web_scraper import SYSTEM_PROMPT

        prompt_parts = []
        if history:
            prompt_parts.append("Previous actions this session:")
            for i, step in enumerate(history[-10:], 1):
                prompt_parts.append(f"  {i}. {step.action} — {step.reason or ''}")
                if step.error:
                    prompt_parts.append(f"     ERROR: {step.error}")
            prompt_parts.append("")

        prompt_parts.append(f"GOAL: {goal}")
        prompt_parts.append("")
        prompt_parts.append(page_context)
        prompt_parts.append("")
        prompt_parts.append("What is the next action?")

        user_prompt = "\n".join(prompt_parts)
        text = call_claude_cli(SYSTEM_PROMPT, user_prompt)

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

    def _execute_action(self, action: ScraperAction) -> Optional[str]:
        """Execute a browser action. Returns error string or None."""
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
            return "Element became stale"
        except NoSuchElementException as e:
            return f"Element not found: {e}"
        except Exception as e:
            return f"Action error: {e}"

        return None

    def _run_ai_crawl(self, goal: str, start_url: str) -> SmartCrawlResult:
        """Run an AI-guided crawl, recording all steps for recipe generation."""
        logger.info(f"Running AI-guided crawl: {goal}")
        self.steps = []
        self._init_browser()

        try:
            self.dm.get(start_url)
            time.sleep(2)

            for step_num in range(1, self.max_steps + 1):
                logger.info(f"  AI Step {step_num}/{self.max_steps}")

                page_context = self._get_page_context()
                action = self._ask_ai(goal, page_context, self.steps)

                logger.info(f"    AI decided: {action.action} — {action.reason or ''}")

                step = ScraperStep(
                    step=step_num,
                    url=self.dm.get_current_url(),
                    action=action.action,
                    selector=action.selector,
                    text=action.text,
                    data=action.data,
                    result=action.result,
                    reason=action.reason,
                )

                if action.action == "done":
                    self.steps.append(step)
                    apis = self._discover_apis()
                    return SmartCrawlResult(
                        success=True,
                        result=action.result,
                        data=action.data,
                        steps=self.steps,
                        discovered_apis=apis,
                    )

                if action.action == "fail":
                    step.error = action.reason
                    self.steps.append(step)
                    apis = self._discover_apis()
                    return SmartCrawlResult(
                        success=False,
                        error=action.reason,
                        steps=self.steps,
                        discovered_apis=apis,
                    )

                if action.action == "extract":
                    self.steps.append(step)
                    continue

                error = self._execute_action(action)
                step.error = error
                self.steps.append(step)

                if error:
                    logger.warning(f"    Action error: {error}")

            apis = self._discover_apis()
            return SmartCrawlResult(
                success=False,
                error=f"Reached max steps ({self.max_steps})",
                steps=self.steps,
                discovered_apis=apis,
            )

        except Exception as e:
            logger.exception("AI crawl failed")
            apis = self._discover_apis()
            return SmartCrawlResult(success=False, error=str(e), steps=self.steps, discovered_apis=apis)

    # ------------------------------------------------------------------
    # Recipe generation from AI crawl
    # ------------------------------------------------------------------

    def _generate_recipe(self, goal: str, start_url: str, steps: List[ScraperStep]) -> Optional[CrawlerRecipe]:
        """Use AI to generate a deterministic recipe from a successful crawl session."""
        domain = urlparse(start_url).netloc

        # Build session description for AI
        session_desc = f"GOAL: {goal}\nSTART URL: {start_url}\nDOMAIN: {domain}\n\n"
        session_desc += "SUCCESSFUL CRAWL STEPS:\n"
        for s in steps:
            err = f" [ERROR: {s.error}]" if s.error else ""
            session_desc += f"  Step {s.step}: {s.action}"
            if s.selector:
                session_desc += f" selector='{s.selector}'"
            if s.text:
                session_desc += f" text='{s.text}'"
            if s.url != start_url:
                session_desc += f" url='{s.url}'"
            if s.data:
                session_desc += f" data={json.dumps(s.data)}"
            if s.reason:
                session_desc += f" — {s.reason}"
            session_desc += f"{err}\n"

        try:
            text = call_claude_cli(RECIPE_GEN_PROMPT, session_desc)

            # Parse JSON
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
                logger.error(f"Recipe generation returned invalid JSON: {text[:300]}")
                return None

            # Build recipe object
            recipe_steps = []
            for s in raw.get("steps", []):
                recipe_steps.append(RecipeStep(
                    action=s.get("action", ""),
                    selector=s.get("selector"),
                    text=s.get("text"),
                    url=s.get("url"),
                    seconds=s.get("seconds"),
                    extract_fields=s.get("extract_fields"),
                    description=s.get("description", ""),
                    fallback_selectors=s.get("fallback_selectors", []),
                    optional=s.get("optional", False),
                    wait_after=s.get("wait_after", 1.0),
                ))

            rid = self._recipe_id(domain, goal)
            recipe = CrawlerRecipe(
                recipe_id=rid,
                domain=domain,
                goal=goal,
                start_url=start_url,
                steps=recipe_steps,
                extract_fields=raw.get("extract_fields"),
                created_at=int(time.time()),
                last_used=int(time.time()),
                times_used=1,
                times_succeeded=1,
                version=1,
            )

            self._save_recipe(recipe)
            logger.info(f"Generated recipe {rid} with {len(recipe_steps)} steps")
            return recipe

        except Exception as e:
            logger.exception("Recipe generation failed")
            return None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def crawl(
        self,
        goal: str,
        start_url: str,
        variables: Dict[str, str] = None,
        force_ai: bool = False,
    ) -> SmartCrawlResult:
        """
        Smart crawl: use recipe if available, otherwise AI-guided + generate recipe.

        Args:
            goal: What to find/do
            start_url: Starting URL
            variables: Dynamic values for recipe placeholders {variable}
            force_ai: Force AI-guided crawl even if recipe exists

        Returns:
            SmartCrawlResult with data, steps, and recipe info.
        """
        domain = urlparse(start_url).netloc

        # Step 1: Try recipe first (unless forced AI)
        if not force_ai:
            recipe = self._load_recipe(domain, goal)
            if recipe:
                logger.info(f"Found recipe for '{goal}' on {domain} — running deterministically")
                result = self._run_recipe(recipe, variables)

                if result.success:
                    return result

                # Recipe failed — fall back to AI
                logger.warning(f"Recipe failed: {result.error} — falling back to AI")
                recipe.times_used += 1
                recipe.ai_fallback_count += 1
                recipe.last_used = int(time.time())
                self._save_recipe(recipe)

                # Close and re-init browser for clean AI run
                self.close()

        # Step 2: AI-guided crawl
        result = self._run_ai_crawl(goal, start_url)

        if result.success:
            # Step 3: Generate recipe from successful crawl
            logger.info("AI crawl succeeded — generating recipe...")
            recipe = self._generate_recipe(goal, start_url, result.steps)
            if recipe:
                result.recipe_generated = True
                result.recipe_id = recipe.recipe_id
                logger.info(f"Recipe {recipe.recipe_id} generated and saved!")
            else:
                logger.warning("Recipe generation failed — AI crawl result is still valid")

        return result

    def list_recipes(self) -> List[CrawlerRecipe]:
        """List all saved recipes."""
        recipes = []
        if not os.path.exists(RECIPES_DIR):
            return recipes
        for fname in os.listdir(RECIPES_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(RECIPES_DIR, fname)) as f:
                        recipes.append(CrawlerRecipe.from_dict(json.load(f)))
                except Exception:
                    pass
        return recipes

    def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a recipe by ID."""
        path = self._recipe_path(recipe_id)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Deleted recipe {recipe_id}")
            return True
        return False


def smart_crawl(
    goal: str,
    start_url: str,
    headless: bool = True,
    max_steps: int = 25,
    variables: Dict[str, str] = None,
    force_ai: bool = False,
) -> SmartCrawlResult:
    """Convenience function to run a smart crawl and clean up."""
    crawler = SmartCrawler(headless=headless, max_steps=max_steps)
    try:
        return crawler.crawl(goal, start_url, variables=variables, force_ai=force_ai)
    finally:
        crawler.close()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Smart Crawler — self-learning web crawler")
    parser.add_argument("url", help="Starting URL")
    parser.add_argument("goal", help="What to find or accomplish")
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--headful", action="store_true", help="Run with visible browser")
    parser.add_argument("--force-ai", action="store_true", help="Force AI-guided crawl")
    parser.add_argument("--list-recipes", action="store_true", help="List all saved recipes")
    parser.add_argument("--delete-recipe", type=str, help="Delete a recipe by ID")
    args = parser.parse_args()

    if args.list_recipes:
        crawler = SmartCrawler()
        recipes = crawler.list_recipes()
        if not recipes:
            print("No recipes saved.")
        for r in recipes:
            rate = f"{r.times_succeeded}/{r.times_used}" if r.times_used else "0/0"
            print(f"  {r.recipe_id}  {r.domain:30s}  {r.goal[:50]:50s}  success={rate}  steps={len(r.steps)}")
        sys.exit(0)

    if args.delete_recipe:
        crawler = SmartCrawler()
        if crawler.delete_recipe(args.delete_recipe):
            print(f"Deleted recipe {args.delete_recipe}")
        else:
            print(f"Recipe {args.delete_recipe} not found")
        sys.exit(0)

    result = smart_crawl(
        goal=args.goal,
        start_url=args.url,
        headless=not args.headful,
        max_steps=args.max_steps,
        force_ai=args.force_ai,
    )

    print("\n" + "=" * 60)
    print(f"SUCCESS: {result.success}")
    print(f"USED RECIPE: {result.used_recipe}")
    print(f"RECIPE GENERATED: {result.recipe_generated}")
    if result.recipe_id:
        print(f"RECIPE ID: {result.recipe_id}")
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

    if result.discovered_apis:
        print(f"\n{'=' * 60}")
        print(f"DISCOVERED API ENDPOINTS ({len(result.discovered_apis)}):")
        for i, api in enumerate(result.discovered_apis, 1):
            auth = (
                "NO AUTH" if api.works_without_auth
                else "COOKIES" if api.works_with_cookies
                else "AUTH REQUIRED"
            )
            print(f"\n  {i}. [{auth}] {api.method} {api.url}")
            print(f"     Content-Type: {api.content_type}")
            if api.response_preview:
                preview = api.response_preview[:200].replace("\n", " ")
                print(f"     Preview: {preview}")
            if api.notes:
                print(f"     Notes: {api.notes}")
