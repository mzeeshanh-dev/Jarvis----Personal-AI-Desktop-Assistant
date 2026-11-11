"""Paths and the typed application settings model."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _default_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Jarvis"


def _default_resources_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "resources"
    return Path(__file__).resolve().parents[2] / "resources"


class Paths:
    """Filesystem locations. Electron passes them via environment variables."""

    def __init__(self, data_dir: Path | None = None, resources_dir: Path | None = None) -> None:
        self.data = Path(data_dir or os.environ.get("JARVIS_DATA_DIR") or _default_data_dir()).resolve()
        self.resources = Path(resources_dir or os.environ.get("JARVIS_RESOURCES_DIR") or _default_resources_dir()).resolve()
        self.database = self.data / "jarvis.db"
        self.logs = self.data / "logs"
        self.models = self.data / "models"
        self.browser_profile = self.data / "browser-profile"
        self.screenshots = self.data / "screenshots"

    def ensure(self) -> None:
        for p in (self.data, self.logs, self.models, self.screenshots):
            p.mkdir(parents=True, exist_ok=True)

    def model_file(self, *parts: str) -> Path | None:
        """Find a model in user data first (downloaded), then bundled resources."""
        for base in (self.models, self.resources / "models"):
            candidate = base.joinpath(*parts)
            if candidate.exists():
                return candidate
        return None


def _home(*parts: str) -> str:
    return str(Path.home().joinpath(*parts))


class GeneralSettings(BaseModel):
    shortcut: str = "Control+Space"
    launch_at_login: bool = False
    close_to_tray: bool = True
    minimize_to_tray: bool = True
    float_always_on_top: bool = True
    show_float_on_voice: bool = True
    user_name: str = ""


class VoiceSettings(BaseModel):
    input_device: int | None = None
    wake_word_enabled: bool = False
    wake_word_threshold: float = Field(0.5, ge=0.05, le=0.99)
    vad_threshold: float = Field(0.5, ge=0.1, le=0.95)
    silence_ms: int = Field(900, ge=300, le=4000)
    max_record_s: int = Field(30, ge=3, le=120)
    no_speech_timeout_s: int = Field(7, ge=2, le=30)
    noise_reduction: bool = True
    language: str = "en"
    tts_engine: Literal["piper", "system", "off"] = "piper"
    piper_voice: str = "en_GB-alan-medium"
    speak_responses: bool = True
    speak_typed_responses: bool = False
    tts_rate: float = Field(1.0, ge=0.5, le=2.0)


class ModelPreferences(BaseModel):
    """Defaults verified against Groq's /models on 2026-11-04. If a configured
    model disappears, ProviderManager.resolve_model() falls back automatically."""

    simple: str = "openai/gpt-oss-20b"
    complex: str = "openai/gpt-oss-120b"
    summarization: str = "openai/gpt-oss-120b"
    transcription: str = "whisper-large-v3-turbo"


class LocalLimits(BaseModel):
    """User-entered limits from the Groq console, used for locally estimated quotas
    the API does not report in headers (RPM, TPD, ASH, ASD)."""

    rpm: int | None = None
    tpd: int | None = None
    ash: int | None = None
    asd: int | None = None


class ProviderSettings(BaseModel):
    quota_threshold: float = Field(0.97, ge=0.5, le=1.0)
    request_timeout_s: float = Field(60, ge=5, le=300)
    max_attempts: int = Field(4, ge=1, le=10)
    # When every key is frozen but one recovers within this many seconds
    # (typical for per-minute token windows), wait instead of failing.
    max_wait_s: float = Field(20, ge=0, le=120)
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    models: ModelPreferences = ModelPreferences()
    local_limits: dict[str, LocalLimits] = {}


class PermissionSettings(BaseModel):
    level3_policy: Literal["auto", "ask"] = "ask"
    allowlisted_commands_auto: bool = True
    tool_overrides: dict[str, Literal["auto", "ask", "deny"]] = {}
    workspace_roots: list[str] = Field(default_factory=lambda: [_home("Desktop"), _home("Documents"), _home("Downloads")])
    command_allowlist: list[str] = Field(default_factory=lambda: [
        "git status", "git log", "git diff", "git branch", "git show",
        "node --version", "npm --version", "python --version", "npm ls", "pip list",
        "npm test", "npm run test", "npm run lint", "pytest",
    ])
    approval_timeout_s: int = Field(120, ge=10, le=900)

    @field_validator("workspace_roots")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s.strip()]


class BrowserSettings(BaseModel):
    channel: Literal["msedge", "chrome", "chromium"] = "msedge"
    headless: bool = False
    use_existing_chrome: bool = False
    cdp_url: str = "http://127.0.0.1:9222"
    search_engine: Literal["google", "bing", "duckduckgo"] = "google"


class AutomationSettings(BaseModel):
    max_iterations: int = Field(8, ge=1, le=25)
    tool_timeout_s: int = Field(60, ge=5, le=600)
    command_timeout_s: int = Field(120, ge=5, le=1800)
    max_output_kb: int = Field(200, ge=16, le=4096)
    max_concurrent_tasks: int = Field(2, ge=1, le=6)
    parallel_tools: bool = True


class PerformanceSettings(BaseModel):
    history_messages: int = Field(20, ge=2, le=100)
    context_char_budget: int = Field(24000, ge=4000, le=200000)
    tool_result_char_limit: int = Field(12000, ge=1000, le=100000)


class AppSettings(BaseModel):
    general: GeneralSettings = GeneralSettings()
    voice: VoiceSettings = VoiceSettings()
    providers: ProviderSettings = ProviderSettings()
    permissions: PermissionSettings = PermissionSettings()
    browser: BrowserSettings = BrowserSettings()
    automation: AutomationSettings = AutomationSettings()
    performance: PerformanceSettings = PerformanceSettings()
