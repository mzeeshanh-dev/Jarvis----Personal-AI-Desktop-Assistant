"""Tool modules. ``build_registry`` registers every built-in tool."""
from __future__ import annotations

from agent.tool_registry import ToolRegistry


def build_registry() -> ToolRegistry:
    from tools import apps, browser, email, filesystem, terminal

    reg = ToolRegistry()
    for module in (apps, browser, filesystem, terminal, email):
        module.register(reg)
    return reg
