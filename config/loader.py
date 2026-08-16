"""从 .env 文件和系统环境变量加载应用配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _parse(value: str) -> Any:
    text = value.strip()
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def load_env(path: str | Path | None = None) -> dict[str, Any]:
    env_path = Path(path or Path(__file__).resolve().parents[1] / ".env")
    values: dict[str, Any] = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = _parse(value)
    for key, value in os.environ.items():
        if key.isupper():
            values[key] = _parse(value)
    config: dict[str, Any] = {}
    for key, value in values.items():
        config[key.lower()] = value
    config["defaultDomains"] = values.get("DEFAULT_DOMAINS", config.get("defaultdomains", ""))
    return config
