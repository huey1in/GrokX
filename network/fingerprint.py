#!/usr/bin/env python3
"""Generate a coherent per-session HTTP client profile for protocol requests."""

from __future__ import annotations

from dataclasses import dataclass
import re
import secrets
from typing import Any


# 固定指纹参数
FINGERPRINT_PLATFORMS = ("windows", "macos", "linux")
FINGERPRINT_CHROME_MIN = 126
FINGERPRINT_CHROME_MAX = 135
FINGERPRINT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
FINGERPRINT_FIXED_PLATFORM = "windows"
FINGERPRINT_MODE = "random"
USER_AGENT = ""


@dataclass(frozen=True)
class FingerprintProfile:
    profile_id: str
    mode: str
    platform: str
    browser_major: int
    user_agent: str
    accept_language: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str

    def headers(self) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "accept-language": self.accept_language,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
        }

    def public_metadata(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "mode": self.mode,
            "platform": self.platform,
            "browser_major": self.browser_major,
            "accept_language": self.accept_language,
        }


def _platforms(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        raw = re.split(r"[,;\s]+", value)
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = []
    allowed = tuple(
        item.strip().lower()
        for item in raw
        if item.strip().lower() in {"windows", "macos", "linux"}
    )
    return allowed or ("windows", "macos", "linux")


def _major_from_user_agent(user_agent: str) -> int:
    match = re.search(r"(?:Chrome|CriOS)/(\d+)", user_agent)
    return int(match.group(1)) if match else 131


def _user_agent(platform: str, major: int) -> tuple[str, str]:
    if platform == "macos":
        os_part = "Macintosh; Intel Mac OS X 10_15_7"
        hint = '"macOS"'
    elif platform == "linux":
        os_part = "X11; Linux x86_64"
        hint = '"Linux"'
    else:
        os_part = "Windows NT 10.0; Win64; x64"
        hint = '"Windows"'
    user_agent = (
        f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )
    return user_agent, hint


def build_fingerprint(config: dict[str, Any]) -> FingerprintProfile:
    mode = str(config.get("fingerprint_mode") or FINGERPRINT_MODE).strip().lower()
    accept_language = FINGERPRINT_ACCEPT_LANGUAGE.strip()
    user_agent_override = str(config.get("user_agent") or USER_AGENT).strip()
    if mode == "fixed" and user_agent_override:
        user_agent = user_agent_override
        platform = FINGERPRINT_FIXED_PLATFORM
        if platform not in {"windows", "macos", "linux"}:
            platform = "windows"
        major = _major_from_user_agent(user_agent)
        _, platform_hint = _user_agent(platform, major)
    else:
        mode = "random"
        platform = secrets.choice(_platforms(FINGERPRINT_PLATFORMS))
        minimum = max(120, FINGERPRINT_CHROME_MIN)
        maximum = max(minimum, FINGERPRINT_CHROME_MAX)
        major = minimum + secrets.randbelow(maximum - minimum + 1)
        user_agent, platform_hint = _user_agent(platform, major)
    sec_ch_ua = (
        f'"Chromium";v="{major}", "Google Chrome";v="{major}", '
        '"Not.A/Brand";v="24"'
    )
    return FingerprintProfile(
        profile_id=secrets.token_hex(6),
        mode=mode,
        platform=platform,
        browser_major=major,
        user_agent=user_agent,
        accept_language=accept_language,
        sec_ch_ua=sec_ch_ua,
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform=platform_hint,
    )

