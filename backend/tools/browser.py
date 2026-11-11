"""Browser automation via Playwright.

Uses a dedicated persistent profile (data dir/browser-profile) on the
installed Edge or Chrome, so no browser download is needed and the user's
normal profile, cookies and saved passwords are never touched. Attaching to an
existing Chrome over CDP happens only when explicitly enabled in settings.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal, Optional
from urllib.parse import quote_plus, urlparse

from pydantic import Field, field_validator

from agent.tool_registry import ToolContext, ToolError, ToolInput, ToolRegistry, ToolResult
from core.config import AppSettings, Paths

log = logging.getLogger(__name__)

SEARCH = {"google": "https://www.google.com/search?q={q}", "bing": "https://www.bing.com/search?q={q}",
          "duckduckgo": "https://duckduckgo.com/html/?q={q}"}


def _check_url(v: str) -> str:
    v = v.strip()
    if "://" not in v:
        v = "https://" + v
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("only http(s) URLs are allowed")
    return v


class BrowserManager:
    def __init__(self, paths: Paths, settings: callable) -> None:  # type: ignore[valid-type]
        self.paths = paths
        self._settings = settings
        self._pw: Any = None
        self._context: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self.page: Any = None

    @property
    def running(self) -> bool:
        return self._context is not None

    async def ensure(self) -> Any:
        async with self._lock:
            if self._context is None:
                await self._start()
            if self.page is None or self.page.is_closed():
                pages = [p for p in self._context.pages if not p.is_closed()]
                self.page = pages[-1] if pages else await self._context.new_page()
            return self.page

    async def _start(self) -> None:
        from playwright.async_api import async_playwright  # lazy: heavy import

        s: AppSettings = self._settings()
        self._pw = await async_playwright().start()
        if s.browser.use_existing_chrome:
            self._browser = await self._pw.chromium.connect_over_cdp(s.browser.cdp_url, timeout=8000)
            self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        else:
            self.paths.browser_profile.mkdir(parents=True, exist_ok=True)
            kwargs: dict[str, Any] = {"headless": s.browser.headless, "no_viewport": True, "accept_downloads": False}
            if s.browser.channel != "chromium":
                kwargs["channel"] = s.browser.channel
            self._context = await self._pw.chromium.launch_persistent_context(str(self.paths.browser_profile), **kwargs)
        self._context.on("close", lambda *_: self._on_closed())

    def _on_closed(self) -> None:
        # The user closed the automation window: also stop the Playwright
        # driver so the next ensure() doesn't leak a second one.
        pw, self._pw = self._pw, None
        self._context = self._browser = self.page = None
        if pw is not None:
            asyncio.get_running_loop().create_task(pw.stop())

    async def new_page(self) -> Any:
        await self.ensure()
        self.page = await self._context.new_page()
        return self.page

    async def close(self) -> None:
        async with self._lock:
            try:
                if self._browser is not None:
                    await self._browser.close()  # CDP: disconnects only, the user's Chrome keeps running
                elif self._context is not None:
                    await self._context.close()
            except Exception:  # noqa: BLE001
                log.debug("browser close failed", exc_info=True)
            try:
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = self._context = self._browser = self.page = None


# ------------------------------------------------------------------ inputs
class BrowserOpenIn(ToolInput):
    url: Optional[str] = Field(None, description="Optional URL to open in a new tab")

    @field_validator("url")
    @classmethod
    def _url(cls, v: Optional[str]) -> Optional[str]:
        return _check_url(v) if v else v


class BrowserSearchIn(ToolInput):
    query: str = Field(..., min_length=1, max_length=400)
    engine: Optional[Literal["google", "bing", "duckduckgo"]] = None
    max_results: int = Field(8, ge=1, le=20)


class NavigateIn(ToolInput):
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)


class ReadPageIn(ToolInput):
    max_chars: int = Field(8000, ge=500, le=60000)


class ExtractIn(ToolInput):
    kind: Literal["links", "headings", "tables", "forms", "text"] = "links"
    selector: Optional[str] = Field(None, max_length=300, description="CSS selector to scope extraction")
    max_items: int = Field(50, ge=1, le=300)


class ClickIn(ToolInput):
    role: Optional[Literal["button", "link", "checkbox", "tab", "menuitem", "option", "radio", "combobox", "textbox"]] = None
    name: Optional[str] = Field(None, max_length=200, description="Accessible name (used with role)")
    text: Optional[str] = Field(None, max_length=200, description="Visible text to click")
    selector: Optional[str] = Field(None, max_length=300, description="CSS selector (last resort)")
    exact: bool = False


class FillIn(ToolInput):
    label: Optional[str] = Field(None, max_length=200)
    placeholder: Optional[str] = Field(None, max_length=200)
    selector: Optional[str] = Field(None, max_length=300)
    value: str = Field(..., max_length=10000)
    submit: bool = False


class ScreenshotIn(ToolInput):
    full_page: bool = False


class EmptyIn(ToolInput):
    pass


_EXTRACT_JS = """
([kind, selector, max]) => {
  const root = selector ? document.querySelector(selector) : document.body;
  if (!root) return {error: 'selector matched nothing'};
  const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
  if (kind === 'links') return [...root.querySelectorAll('a[href]')].filter(a => clean(a.innerText)).slice(0, max)
      .map(a => ({text: clean(a.innerText).slice(0, 160), href: a.href}));
  if (kind === 'headings') return [...root.querySelectorAll('h1,h2,h3,h4')].slice(0, max)
      .map(h => ({level: h.tagName, text: clean(h.innerText).slice(0, 200)}));
  if (kind === 'tables') return [...root.querySelectorAll('table')].slice(0, Math.min(max, 10))
      .map(t => [...t.rows].slice(0, 50).map(r => [...r.cells].map(c => clean(c.innerText).slice(0, 120))));
  if (kind === 'forms') return [...root.querySelectorAll('input,textarea,select,button')].slice(0, max)
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || null,
                  label: clean((e.labels && e.labels[0] && e.labels[0].innerText) || e.getAttribute('aria-label') || e.placeholder || e.innerText).slice(0, 120)}));
  return clean(root.innerText).slice(0, 20000);
}
"""

_SERP_JS = """
(max) => {
  const out = [];
  for (const h of document.querySelectorAll('a h3, h2 a, a.result__a')) {
    const a = h.closest('a') || h;
    if (!a.href || a.href.includes('google.com/search')) continue;
    const box = a.closest('div[data-hveid], li, .result') || a.parentElement;
    out.push({title: (h.innerText || a.innerText).trim(), url: a.href,
              snippet: box ? box.innerText.replace(/\\s+/g, ' ').slice(0, 300) : ''});
    if (out.length >= max) break;
  }
  return out;
}
"""


def register(reg: ToolRegistry) -> None:
    async def _page(ctx: ToolContext) -> Any:
        try:
            return await ctx.services.browser.ensure()
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Could not start the automation browser ({type(exc).__name__}: {str(exc)[:200]}). "
                            "Check Settings → Browser (installed Edge/Chrome channel).", code="browser_unavailable") from exc

    async def _goto(page: Any, url: str) -> None:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Navigation to {url} failed: {str(exc).splitlines()[0][:200]}", code="navigation", transient=True) from exc

    @reg.tool("browser_open", "Open the automation browser (optionally a URL in a new tab).",
              input_model=BrowserOpenIn, level=1, timeout_s=45, category="browser")
    async def browser_open(args: BrowserOpenIn, ctx: ToolContext) -> ToolResult:
        await _page(ctx)
        page = await ctx.services.browser.new_page() if args.url else ctx.services.browser.page
        if args.url:
            await _goto(page, args.url)
        return ToolResult.success(f"Browser open{f' at {page.url}' if args.url else ''}.", {"url": page.url, "title": await page.title()})

    @reg.tool("browser_search", "Search the web in the automation browser and return the top results (title, url, snippet).",
              input_model=BrowserSearchIn, level=1, timeout_s=45, idempotent=True, category="browser")
    async def browser_search(args: BrowserSearchIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        engine = args.engine or ctx.settings.browser.search_engine
        await _goto(page, SEARCH[engine].format(q=quote_plus(args.query)))
        try:
            await page.wait_for_load_state("load", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        results = await page.evaluate(_SERP_JS, args.max_results)
        return ToolResult.success(f"{len(results)} results for “{args.query}”.", {"engine": engine, "url": page.url, "results": results},
                                  untrusted=True, source="web")

    @reg.tool("browser_navigate", "Navigate the current browser tab to a URL.", input_model=NavigateIn, level=1, timeout_s=45, category="browser")
    async def browser_navigate(args: NavigateIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        await _goto(page, args.url)
        return ToolResult.success(f"Opened {page.url}.", {"url": page.url, "title": await page.title()})

    @reg.tool("browser_read_page", "Read the visible text of the current browser page.", input_model=ReadPageIn, level=0, timeout_s=30, category="browser")
    async def browser_read_page(args: ReadPageIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        text = await page.evaluate("() => { const m = document.querySelector('main, article, [role=main]') || document.body; return m.innerText }")
        text = " ".join((text or "").split())
        return ToolResult.success(f"Read {min(len(text), args.max_chars)} characters from {page.url}.",
                                  {"url": page.url, "title": await page.title(), "text": text[: args.max_chars], "truncated": len(text) > args.max_chars},
                                  untrusted=True, source="web")

    @reg.tool("browser_extract_content", "Extract structured content (links, headings, tables, form fields or text) from the current page.",
              input_model=ExtractIn, level=0, timeout_s=30, category="browser")
    async def browser_extract_content(args: ExtractIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        data = await page.evaluate(_EXTRACT_JS, [args.kind, args.selector, args.max_items])
        if isinstance(data, dict) and data.get("error"):
            raise ToolError(data["error"], code="no_match")
        n = len(data) if isinstance(data, list) else 1
        return ToolResult.success(f"Extracted {n} {args.kind} item(s) from {page.url}.", {"url": page.url, "items": data},
                                  untrusted=True, source="web")

    def _locator(page: Any, args: ClickIn | FillIn) -> Any:
        if isinstance(args, ClickIn):
            if args.role:
                return page.get_by_role(args.role, name=args.name, exact=args.exact) if args.name else page.get_by_role(args.role)
            if args.text:
                return page.get_by_text(args.text, exact=args.exact)
        else:
            if args.label:
                return page.get_by_label(args.label)
            if args.placeholder:
                return page.get_by_placeholder(args.placeholder)
        if args.selector:
            return page.locator(args.selector)
        raise ToolError("Provide role+name, text/label/placeholder, or selector.", code="invalid_arguments")

    @reg.tool("browser_click", "Click an element on the current page, located by accessible role+name, visible text, or CSS selector.",
              input_model=ClickIn, level=3, timeout_s=30, category="browser",
              describe=lambda a: f"Click {a.role or ''} “{a.name or a.text or a.selector}” in the browser")
    async def browser_click(args: ClickIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        loc = _locator(page, args).first
        try:
            await loc.click(timeout=8000)
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Click failed: {str(exc).splitlines()[0][:200]}", code="no_match") from exc
        return ToolResult.success(f"Clicked. Page is now {page.url}.", {"url": page.url, "title": await page.title()})

    @reg.tool("browser_fill", "Type a value into a form field on the current page (by label, placeholder or selector). Never fills passwords.",
              input_model=FillIn, level=3, timeout_s=30, category="browser",
              describe=lambda a: f"Fill “{a.label or a.placeholder or a.selector}” in the browser" + (" and submit" if a.submit else ""))
    async def browser_fill(args: FillIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        loc = _locator(page, args).first
        try:
            input_type = (await loc.get_attribute("type", timeout=8000) or "").lower()
            if input_type == "password":
                raise ToolError("Jarvis does not fill password fields.", code="blocked")
            await loc.fill(args.value, timeout=8000)
            if args.submit:
                await loc.press("Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Fill failed: {str(exc).splitlines()[0][:200]}", code="no_match") from exc
        return ToolResult.success("Filled the field" + (" and submitted." if args.submit else "."), {"url": page.url})

    @reg.tool("browser_screenshot", "Take a screenshot of the current browser page; returns the saved file path.",
              input_model=ScreenshotIn, level=0, timeout_s=30, category="browser")
    async def browser_screenshot(args: ScreenshotIn, ctx: ToolContext) -> ToolResult:
        page = await _page(ctx)
        path = ctx.services.paths.screenshots / f"page-{time.strftime('%Y%m%d-%H%M%S')}.png"
        await page.screenshot(path=str(path), full_page=args.full_page)
        return ToolResult.success(f"Saved screenshot to {path}.", {"path": str(path), "url": page.url})

    @reg.tool("browser_close", "Close the automation browser.", input_model=EmptyIn, level=1, category="browser")
    async def browser_close(args: EmptyIn, ctx: ToolContext) -> ToolResult:
        await ctx.services.browser.close()
        return ToolResult.success("Closed the browser.")
