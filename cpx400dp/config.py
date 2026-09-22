"""Persisted application settings (connection defaults, poll rate, chart
window, history length). Stored as JSON under ~/.config/cpx400dp-gui/."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "cpx400dp-gui"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"


@dataclass
class AppConfig:
    host: str = "192.168.0.100"
    port: int = 9221
    poll_hz: float = 2.0
    chart_window_s: float = 60.0
    history_max_points: int = 7200
    request_interface_lock_on_connect: bool = False
    poll_limit_status: bool = True
    theme: str = "Light"
    compact_mode: bool = False

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "AppConfig":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
