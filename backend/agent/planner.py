"""Routing: simple commands run deterministically (no LLM call); everything
else goes to the LLM tool-calling loop.

Also chooses which configured model handles an LLM-routed request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from core.config import AppSettings


@dataclass
class DirectPlan:
    tool: str
    arguments: dict[str, Any]
    intent: str
    fallback_to_llm: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


WEBSITES = {
    "google": "https://www.google.com", "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
    "github": "https://github.com", "chatgpt": "https://chatgpt.com", "linkedin": "https://www.linkedin.com",
    "twitter": "https://x.com", "x": "https://x.com", "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com", "reddit": "https://www.reddit.com", "wikipedia": "https://www.wikipedia.org",
    "stack overflow": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
    "google drive": "https://drive.google.com", "google calendar": "https://calendar.google.com",
    "whatsapp web": "https://web.whatsapp.com", "netflix": "https://www.netflix.com",
}

SEARCH_URLS = {
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
}

_PREFIX = re.compile(r"^(?:(?:hey\s+)?jarvis[,!.\s]+)?(?:(?:please|can you|could you|would you|kindly)\s+)*", re.I)
_SUFFIX = re.compile(r"\s*(?:,?\s*(?:please|for me|now|thanks|thank you))+[.!?]*$", re.I)
_COMPLEX = re.compile(r"\b(and|then|after|summari[sz]e|compare|find|which|what|why|how|read|email|file|folder|inbox|tell me|my)\b", re.I)
_DOMAIN = re.compile(r"^(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?$", re.I)

_OPEN = re.compile(r"^(?:open|launch|start|run|fire up|bring up)\s+(?:up\s+)?(?:the\s+)?(?P<target>.+?)(?:\s+(?:app|application|program|website|site))?$", re.I)
_GOTO = re.compile(r"^(?:go to|navigate to|visit|browse to)\s+(?P<target>\S+)$", re.I)
_SEARCH = re.compile(
    r"^(?:(?:search|look up|lookup)\s+(?:(?:on\s+)?(?P<engine1>google|bing|duckduckgo|the web|online)\s+)?(?:for\s+)?(?P<q1>.+?)(?:\s+on\s+(?P<engine2>google|bing|duckduckgo|the web))?"
    r"|google\s+(?P<q2>.+))$", re.I)


def normalize(text: str) -> str:
    t = text.strip()
    t = _PREFIX.sub("", t)
    t = _SUFFIX.sub("", t)
    return t.strip().rstrip(".!?").strip()


def route_direct(text: str, settings: AppSettings) -> DirectPlan | None:
    t = normalize(text)
    if not t or len(t) > 120:
        return None

    if m := _GOTO.match(t):
        target = m["target"]
        if _DOMAIN.match(target) or target.lower() in WEBSITES:
            return DirectPlan("open_url", {"url": _to_url(target)}, "open_url")

    if m := _SEARCH.match(t):
        query = (m["q1"] or m["q2"] or "").strip()
        engine_word = (m["engine1"] or m["engine2"] or "").lower()
        # "search my files/emails for X" etc. need real tools -> LLM.
        if query and not re.search(r"\b(my|email|emails|gmail|inbox|file|files|folder|folders|drive|computer)\b", t, re.I):
            if engine_word or t.lower().startswith("google ") or "for" in t.lower().split()[:3]:
                engine = engine_word if engine_word in SEARCH_URLS else settings.browser.search_engine
                url = SEARCH_URLS[engine].format(q=quote_plus(query))
                return DirectPlan("open_url", {"url": url}, "web_search", meta={"query": query, "engine": engine})

    if m := _OPEN.match(t):
        target = m["target"].strip().strip('"\'')
        low = target.lower()
        if low in WEBSITES:
            return DirectPlan("open_url", {"url": WEBSITES[low]}, "open_url")
        if _DOMAIN.match(target):
            return DirectPlan("open_url", {"url": _to_url(target)}, "open_url")
        if len(target.split()) <= 4 and not _COMPLEX.search(target) and not re.search(r"[\\/:]", target):
            return DirectPlan("open_application", {"name": target}, "open_application")
    return None


def _to_url(target: str) -> str:
    low = target.lower()
    if low in WEBSITES:
        return WEBSITES[low]
    return target if re.match(r"^https?://", target, re.I) else f"https://{target}"


_CATEGORY_HINTS = {
    "browser": r"web|internet|online|google|bing|search for|look up|website|site|page|brows|url|link|https?://|\.com|news|click|form|screenshot|youtube",
    "email": r"e-?mail|mail|gmail|inbox|draft|sender|thread|reply to|unread",
    "terminal": r"\brun\b|command|terminal|\bgit\b|\bnpm\b|\bpip\b|python|node|script|build|\btests?\b|project|process|server|\bdev\b|output|log|error|compile|install|version",
    "filesystem": r"file|folder|director|pdf|docx?|document|read|write|create|edit|save|move|copy|delete|rename|notes|summar|workspace|desktop|downloads|find|txt|code|readme",
}


def tool_categories(text: str, prior: set[str] | None = None) -> set[str]:
    """Pick the tool categories a request plausibly needs. Each tool schema costs
    prompt tokens on every LLM call, so sending all 31 tools wastes quota on
    small (e.g. 8k TPM) Groq plans. Apps are always included; categories used
    earlier in the conversation stay available for follow-ups."""
    low = text.lower()
    cats = {"apps"} | {c for c, pat in _CATEGORY_HINTS.items() if re.search(pat, low)}
    cats |= prior or set()
    if cats == {"apps"}:
        cats |= {"filesystem", "browser"}  # generic request: the most common helpers
    return cats


_HARD = re.compile(r"\b(and then|then|compare|summari[sz]e|analy[sz]e|diagnose|debug|explain|why|plan|pending|latest|each|all)\b", re.I)


def choose_model(text: str, settings: AppSettings) -> tuple[str, str]:
    """Return (model, tier). Short single-step requests use the 'simple' model."""
    words = len(text.split())
    if words <= 10 and not _HARD.search(text) and text.count(",") == 0:
        return settings.providers.models.simple, "simple"
    return settings.providers.models.complex, "complex"
