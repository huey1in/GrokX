#!/usr/bin/env python3
"""Browser-free provider adapters for the protocol registration flow."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
from typing import Any

from curl_cffi import requests

import providers.mail as mail_moemail


def extract_code(text: str, subject: str = "") -> str | None:
    pattern = r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b"
    for value in (subject, text):
        match = re.search(pattern, str(value or ""), re.IGNORECASE)
        if match:
            return match.group(1)
    for value in (text, subject):
        match = re.search(
            r"(?:verification|your|confirmation)\s+code[:\s]+(\d{4,8})",
            str(value or ""),
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
    return None


@dataclass
class MoeMailProvider:
    api_base: str
    api_key: str
    domain: str = ""
    expiry_time: int = 86_400_000
    proxies: dict[str, str] | None = None

    def create(self) -> tuple[str, str]:
        return mail_moemail.new_address(
            self.api_base,
            self.api_key,
            domain=self.domain,
            expiry_time=self.expiry_time,
            proxies=self.proxies,
        )

    def wait_code(self, token: str, email: str) -> str:
        return mail_moemail.poll_code(
            self.api_base,
            self.api_key,
            token,
            email,
            extract=extract_code,
            timeout=180,
            poll_interval=3,
            proxies=self.proxies,
        )


class CastleTokenProvider:
    """Read stage-specific tokens or request them from a configured HTTP supplier."""

    def __init__(
        self,
        *,
        email_token: str = "",
        final_token: str = "",
        provider_url: str = "",
        provider_key: str = "",
        session: Any = None,
    ):
        self.tokens = {"email": str(email_token or ""), "final": str(final_token or "")}
        self.provider_url = str(provider_url or "").strip()
        self.provider_key = str(provider_key or "").strip()
        self.session = session or requests

    def acquire(self, *, stage: str, email: str) -> str:
        static = self.tokens.get(stage, "").strip()
        if static:
            return static
        if not self.provider_url:
            raise RuntimeError(f"missing CASTLE_REQUEST_TOKEN supplier for stage={stage}")
        headers = {"content-type": "application/json"}
        if self.provider_key:
            headers["authorization"] = f"Bearer {self.provider_key}"
        response = self.session.post(
            self.provider_url,
            json={"stage": stage, "email": email, "flow": "signup", "method": "email_password"},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        token = str(data.get("token") or "").strip() if isinstance(data, dict) else ""
        if not token:
            raise RuntimeError("Castle token supplier returned no token")
        return token


class CastleSdkTokenProvider:
    """使用 Castle 官方 JavaScript SDK 动态生成 Request Token。"""

    def __init__(self, publishable_key: str, page_url: str, user_agent: str = ""):
        self.publishable_key = publishable_key.strip()
        self.page_url = page_url.strip()
        self.user_agent = user_agent.strip()
        self.script = Path(__file__).parent / "castle_sdk" / "mint.mjs"
        self.node_binary = shutil.which("node") or shutil.which("nodejs")
        if not self.node_binary:
            raise RuntimeError(
                "Castle SDK 需要 Node.js 22.13+；请先安装 Node.js，"
                "再在 providers/castle_sdk 目录运行 npm ci"
            )
        self._tokens: list[str] = []
        self._token_lock = threading.Lock()

    def _mint(self, count: int) -> list[str]:
        completed = subprocess.run(
            [
                self.node_binary,
                str(self.script),
                "--pk",
                self.publishable_key,
                "--url",
                self.page_url,
                "--user-agent",
                self.user_agent,
                "--count",
                str(count),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Castle SDK 生成失败: {completed.stderr.strip()[:300]}")
        for line in reversed(completed.stdout.splitlines()):
            try:
                tokens = json.loads(line).get("tokens") or []
            except Exception:
                continue
            tokens = [str(t).strip() for t in tokens if str(t).strip()]
            if tokens:
                return tokens
        raise RuntimeError("Castle SDK 未返回有效 Token")

    def acquire(self, *, stage: str, email: str) -> str:
        with self._token_lock:
            if not self._tokens:
                self._tokens = self._mint(2)
            return self._tokens.pop(0)
