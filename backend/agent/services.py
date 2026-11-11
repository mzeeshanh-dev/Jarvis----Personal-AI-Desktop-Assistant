"""Container of long-lived backend services shared by the agent and tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.permissions import ApprovalBroker
    from core.config import AppSettings, Paths
    from core.events import EventBus
    from core.perf import PerfMonitor
    from core.secrets import SecretStore
    from core.settings_store import SettingsStore
    from integrations.gmail import GmailService
    from providers.groq_client import GroqClient
    from providers.manager import ProviderManager
    from storage.database import Database
    from tools.browser import BrowserManager
    from tools.terminal import ProcessManager


@dataclass
class Services:
    paths: "Paths"
    db: "Database"
    settings_store: "SettingsStore"
    events: "EventBus"
    secrets: "SecretStore"
    providers: "ProviderManager"
    groq: "GroqClient"
    approvals: "ApprovalBroker"
    perf: "PerfMonitor"
    browser: "BrowserManager"
    processes: "ProcessManager"
    gmail: "GmailService"
    voice: Any = None  # voice.pipeline.VoiceService, attached after construction

    @property
    def settings(self) -> "AppSettings":
        return self.settings_store.current
