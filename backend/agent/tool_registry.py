"""Tool definitions and the registry the agent exposes to the LLM."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from agent.services import Services
    from core.config import AppSettings


class ToolInput(BaseModel):
    """Base for tool argument models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolError(Exception):
    """Expected, user-presentable tool failure."""

    def __init__(self, message: str, *, code: str = "error", transient: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.transient = transient


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: Any = None
    error: str | None = None
    code: str | None = None
    untrusted: bool = False
    source: str | None = None

    @classmethod
    def success(cls, summary: str, data: Any = None, *, untrusted: bool = False, source: str | None = None) -> "ToolResult":
        return cls(True, summary, data, untrusted=untrusted, source=source)

    @classmethod
    def failure(cls, error: str, code: str = "error") -> "ToolResult":
        return cls(False, error, None, error=error, code=code)


@dataclass
class Assessment:
    """Dynamic, argument-dependent permission assessment."""

    level: int
    auto_ok: bool = False
    reason: str | None = None


@dataclass
class ToolContext:
    task_id: str
    conversation_id: str | None
    settings: "AppSettings"
    services: "Services"
    tainted: bool = False
    tainted_by: list[str] = field(default_factory=list)


Handler = Callable[[Any, ToolContext], Awaitable[ToolResult]]
Assessor = Callable[[Any, ToolContext], Assessment]
Describer = Callable[[Any], str]


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    level: int
    handler: Handler
    timeout_s: float | None = None
    idempotent: bool = False
    assess: Assessor | None = None
    describe: Describer | None = None
    category: str = "general"

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        return _strip_titles(schema)

    def openai_spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.json_schema()}}


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._available: dict[str, Callable[[], bool]] = {}

    def register(self, tool: Tool, available: Callable[[], bool] | None = None) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        if not 0 <= tool.level <= 5:
            raise ValueError("permission level must be 0..5")
        self._tools[tool.name] = tool
        if available:
            self._available[tool.name] = available
        return tool

    def tool(self, name: str, description: str, *, input_model: type[ToolInput], level: int,
             timeout_s: float | None = None, idempotent: bool = False, category: str = "general",
             assess: Assessor | None = None, describe: Describer | None = None) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.register(Tool(name, description, input_model, level, fn, timeout_s, idempotent, assess, describe, category))
            return fn
        return deco

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def is_available(self, name: str) -> bool:
        check = self._available.get(name)
        return True if check is None else check()

    def schemas(self, categories: set[str] | None = None) -> list[dict[str, Any]]:
        """Tool specs for the LLM, optionally limited to some categories (each
        schema costs prompt tokens on every request)."""
        return [t.openai_spec() for t in self._tools.values()
                if self.is_available(t.name) and (categories is None or t.category in categories)]

    def category_of(self, name: str) -> str | None:
        t = self._tools.get(name)
        return t.category if t else None

    def catalog(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "level": t.level, "category": t.category,
                 "available": self.is_available(t.name), "schema": t.json_schema()} for t in self._tools.values()]
