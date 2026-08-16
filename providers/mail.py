#!/usr/bin/env python3
"""MoeMail OpenAPI provider.

Official API:
    GET  /api/config
    POST /api/emails/generate
    GET  /api/emails/{email_id}
    GET  /api/emails/{email_id}/{message_id}
"""

from __future__ import annotations

import json
import re
import secrets
import string
import time
from urllib.parse import quote


VALID_EXPIRY_TIMES = {0, 3_600_000, 86_400_000, 604_800_000}


def _session(api_key: str, proxies=None):
    from curl_cffi import requests

    kwargs = {"impersonate": "chrome120", "headers": {"X-API-Key": api_key}}
    if proxies:
        kwargs.update({"proxies": proxies, "verify": False})
    return requests.Session(**kwargs)


def _json_response(response, action: str) -> dict:
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        try:
            data = json.loads(response.text)
        except Exception as exc:
            raise Exception(f"MoeMail {action} 返回非 JSON: {response.text[:200]}") from exc
    if not isinstance(data, dict):
        raise Exception(f"MoeMail {action} 返回格式错误: {str(data)[:200]}")
    if data.get("success") is False or data.get("error"):
        raise Exception(f"MoeMail {action} 失败: {data.get('error') or str(data)[:200]}")
    return data


def _request_json(session, method: str, url: str, action: str, attempts: int = 3, **kwargs) -> dict:
    """Perform a MoeMail request with bounded retries for transient failures."""
    last_exc = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = session.request(method, url, **kwargs)
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 429 or status >= 500:
                raise Exception(f"MoeMail {action} 暂时不可用: HTTP {status}")
            return _json_response(response, action)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
    raise Exception(f"MoeMail {action} 连续 {attempts} 次请求失败: {last_exc}") from last_exc


def _random_name(length: int = 12) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(6, length)))


def get_domains(api_base: str, api_key: str, proxies=None) -> list[str]:
    session = _session(api_key, proxies=proxies)
    data = _request_json(
        session,
        "GET",
        f"{api_base.rstrip('/')}/api/config",
        "获取域名",
        timeout=20,
    )
    raw = data.get("emailDomains") or data.get("email_domains") or data.get("domains") or ""
    if isinstance(raw, str):
        return [item.strip() for item in re.split(r"[,，\s]+", raw) if item.strip()]
    if isinstance(raw, list):
        domains = []
        for item in raw:
            value = item.get("domain") if isinstance(item, dict) else item
            if value and str(value).strip():
                domains.append(str(value).strip())
        return domains
    return []


def new_address(
    api_base: str,
    api_key: str,
    domain: str = "",
    expiry_time: int = 86_400_000,
    proxies=None,
    reserve=None,
    attempts: int = 10,
):
    """Create a MoeMail inbox and return ``(email, moemail:<email_id>)``."""
    base = str(api_base or "").strip().rstrip("/")
    key = str(api_key or "").strip()
    if not base:
        raise Exception("MoeMail API Base 未配置")
    if not key:
        raise Exception("MoeMail API Key 未配置")

    selected_domain = str(domain or "").strip()
    if not selected_domain:
        domains = get_domains(base, key, proxies=proxies)
        if not domains:
            raise Exception("MoeMail 未返回可用邮箱域名")
        selected_domain = domains[0]

    try:
        ttl = int(expiry_time)
    except (TypeError, ValueError):
        ttl = 86_400_000
    if ttl not in VALID_EXPIRY_TIMES:
        ttl = 86_400_000

    session = _session(key, proxies=proxies)
    last = ""
    for _ in range(max(1, attempts)):
        payload = {"name": _random_name(), "expiryTime": ttl, "domain": selected_domain}
        data = _request_json(
            session,
            "POST",
            f"{base}/api/emails/generate",
            "创建邮箱",
            json=payload,
            timeout=20,
        )
        email_id = str(data.get("id") or data.get("emailId") or "").strip()
        email = str(data.get("email") or data.get("address") or "").strip()
        last = email
        if not email_id or not email:
            raise Exception(f"MoeMail 创建邮箱响应缺少 id/email: {str(data)[:200]}")
        if reserve is None or reserve(email):
            return email, f"moemail:{email_id}"
    raise Exception(f"MoeMail 连续创建到重复邮箱，最后一个: {last}")


def _email_id(token: str) -> str:
    value = str(token or "").strip()
    if value.startswith("moemail:"):
        value = value.split(":", 1)[1]
    if not value:
        raise Exception("MoeMail 邮箱 ID 为空")
    return value


def list_messages(api_base: str, api_key: str, token: str, proxies=None) -> list[dict]:
    email_id = quote(_email_id(token), safe="")
    session = _session(api_key, proxies=proxies)
    data = _request_json(
        session,
        "GET",
        f"{api_base.rstrip('/')}/api/emails/{email_id}",
        "读取邮件列表",
        timeout=20,
    )
    messages = data.get("messages") or []
    return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []


def read_message(api_base: str, api_key: str, token: str, message_id: str, proxies=None) -> dict:
    email_id = quote(_email_id(token), safe="")
    mid = quote(str(message_id), safe="")
    session = _session(api_key, proxies=proxies)
    data = _request_json(
        session,
        "GET",
        f"{api_base.rstrip('/')}/api/emails/{email_id}/{mid}",
        "读取邮件正文",
        timeout=20,
    )
    message = data.get("message") or data.get("email") or data
    return message if isinstance(message, dict) else {}


def _mail_text(message: dict) -> str:
    parts = []
    for key in ("content", "text", "body_text", "html", "body_html", "body"):
        value = message.get(key) if isinstance(message, dict) else None
        if isinstance(value, str) and value.strip():
            parts.append(re.sub(r"<[^>]+>", " ", value) if "html" in key else value)
    return "\n".join(parts)


def poll_code(
    api_base: str,
    api_key: str,
    token: str,
    email: str,
    extract,
    timeout: int = 180,
    poll_interval: int = 3,
    proxies=None,
    log_callback=None,
    cancel_callback=None,
    sleep_fn=None,
    raise_if_cancelled=None,
):
    deadline = time.time() + timeout
    seen: dict[str, int] = {}

    def _sleep(seconds):
        sleep_fn(seconds, cancel_callback) if sleep_fn else time.sleep(seconds)

    def _check_cancel():
        if raise_if_cancelled:
            raise_if_cancelled(cancel_callback)
        elif cancel_callback and cancel_callback():
            raise Exception("用户停止注册")

    while time.time() < deadline:
        _check_cancel()
        try:
            messages = list_messages(api_base, api_key, token, proxies=proxies)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] MoeMail 读取邮件列表失败: {exc}")
            _sleep(poll_interval)
            continue

        if log_callback:
            log_callback(f"[Debug] MoeMail 收件箱数量: {len(messages)}")
        for message in messages:
            message_id = str(message.get("id") or message.get("messageId") or "").strip()
            if not message_id or seen.get(message_id, 0) >= 5:
                continue
            seen[message_id] = seen.get(message_id, 0) + 1
            subject = str(message.get("subject") or "")
            sender = str(message.get("from_address") or message.get("fromAddress") or "")
            text = _mail_text(message)
            if not text:
                try:
                    detail = read_message(api_base, api_key, token, message_id, proxies=proxies)
                    subject = subject or str(detail.get("subject") or "")
                    sender = sender or str(detail.get("from_address") or detail.get("fromAddress") or "")
                    text = _mail_text(detail)
                except Exception as exc:
                    if log_callback:
                        log_callback(f"[Debug] MoeMail 读取邮件正文失败 id={message_id}: {exc}")
            if log_callback:
                log_callback(f"[Debug] MoeMail 收到邮件: {subject} ({sender})")
            code = extract(text, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] MoeMail 从邮件中提取到验证码: {code}")
                return code
        _sleep(poll_interval)

    raise Exception(f"MoeMail 在 {timeout}s 内未收到验证码邮件: {email}")
