"""
Shared dataclass models for the smart-scraper package.

All data models extend DataclassBase for consistent from_dict/to_dict support.
"""

from __future__ import annotations

import json
import time
import types
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Union, get_args, get_origin, get_type_hints


class DataclassBase:
    """Base class providing from_dict and to_dict methods for dataclasses."""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        def _is_union_type(t) -> bool:
            o = get_origin(t)
            _union_types = (Union,)
            if hasattr(types, "UnionType"):
                _union_types = (Union, types.UnionType)
            return o in _union_types

        def _strip_none_from_union(t):
            if not _is_union_type(t):
                return t
            args = [a for a in get_args(t) if a is not type(None)]
            return args[0] if len(args) == 1 else t

        def _coerce(value, t):
            if value is None and _is_union_type(t) and type(None) in get_args(t):
                return None
            t = _strip_none_from_union(t)
            origin = get_origin(t)
            args = get_args(t)

            if origin is list and args:
                item_t = _strip_none_from_union(args[0])
                if is_dataclass(item_t):
                    return [
                        item_t.from_dict(v) if isinstance(v, dict) else v
                        for v in (value or [])
                    ]
                return value

            if is_dataclass(t) and isinstance(value, dict):
                return t.from_dict(value)

            return value

        try:
            resolved_hints = get_type_hints(cls)
        except Exception:
            resolved_hints = {}

        kwargs = {}
        for f in fields(cls):
            if f.name in data:
                hint = resolved_hints.get(f.name, f.type)
                kwargs[f.name] = _coerce(data[f.name], hint)

        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """Convert instance to dictionary, handling nested dataclasses."""
        result = {}
        for f in fields(self):
            value = getattr(self, f.name)

            if hasattr(value, "__dataclass_fields__"):
                result[f.name] = value.to_dict()
            elif (
                isinstance(value, list)
                and value
                and hasattr(value[0], "__dataclass_fields__")
            ):
                result[f.name] = [item.to_dict() for item in value]
            else:
                result[f.name] = value

        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str):
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# Web Scraper models
# ---------------------------------------------------------------------------


@dataclass
class ScraperAction(DataclassBase):
    """A single action decided by the AI for the web scraper."""

    action: str = ""
    selector: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    seconds: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class ScraperStep(DataclassBase):
    """A recorded step in a scrape session: the action taken plus context."""

    step: int = 0
    url: str = ""
    action: str = ""
    selector: Optional[str] = None
    text: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ScrapeResult(DataclassBase):
    """Final result of an AI-powered scrape session."""

    success: bool = False
    data: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    steps: List[ScraperStep] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Smart Crawler / Recipe models
# ---------------------------------------------------------------------------


@dataclass
class RecipeStep(DataclassBase):
    """A single deterministic step in a crawler recipe."""

    action: str = ""  # click, type, scroll_down, scroll_up, goto, wait, extract
    selector: Optional[str] = None
    text: Optional[str] = None  # for type action; supports {variable} placeholders
    url: Optional[str] = None  # for goto action; supports {variable} placeholders
    seconds: Optional[int] = None  # for wait action
    extract_fields: Optional[Dict[str, str]] = None  # field_name -> css selector
    description: str = ""  # human-readable description of what this step does
    fallback_selectors: List[str] = field(default_factory=list)  # alternative selectors
    optional: bool = False  # if True, failure doesn't abort the recipe
    wait_after: float = 1.0  # seconds to wait after executing this step


@dataclass
class CrawlerRecipe(DataclassBase):
    """A deterministic recipe generated from an AI-guided crawl session."""

    recipe_id: str = ""  # unique ID (domain + goal hash)
    domain: str = ""  # e.g. "example.com"
    goal: str = ""  # original goal text
    start_url: str = ""  # starting URL
    steps: List[RecipeStep] = field(default_factory=list)
    extract_fields: Optional[Dict[str, str]] = None  # final extraction selectors
    created_at: int = 0  # unix timestamp
    last_used: int = 0  # unix timestamp
    times_used: int = 0
    times_succeeded: int = 0
    ai_fallback_count: int = 0  # how many times AI fallback was needed
    version: int = 1


@dataclass
class SmartCrawlResult(DataclassBase):
    """Result from a SmartCrawler run."""

    success: bool = False
    data: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    steps: List[ScraperStep] = field(default_factory=list)
    error: Optional[str] = None
    used_recipe: bool = False  # True if deterministic recipe was used
    recipe_id: Optional[str] = None
    ai_fallback_used: bool = False  # True if AI had to take over mid-recipe
    recipe_generated: bool = False  # True if a new recipe was created from this run
