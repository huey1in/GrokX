#!/usr/bin/env python3
"""Browser-free CLI entrypoint for the protocol registration pipeline."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from config.loader import load_env
from registration.protocol_client import AuthProtocolClient
from providers.capsolver import CapSolverProvider
from providers.castle import CastleSdkTokenProvider, CastleTokenProvider, MoeMailProvider
from network.fingerprint import build_fingerprint
from registration.flow import (
    ProtocolRegistrationConfig,
    ProtocolRegistrationFlow,
)
from network.proxy import normalize_proxy_url, redact_proxy_url

PROTOCOL_TARGET_BASE = "https://accounts.x.ai"
PROTOCOL_TURNSTILE_SITEKEY = "0x4AAAAAAAhr9JGVDZbrZOo0"
PROTOCOL_CASTLE_PUBLISHABLE_KEY = "pk_p8GGWvD3TmFJZRsX3BQcqAv9aFVispNz"

STAGE_LABELS = {
    "init": "初始化注册任务",
    "protocol_session_bootstrapped": "建立协议会话",
    "email_created": "创建临时邮箱",
    "email_anti_abuse_token_ready": "生成邮件阶段 Castle Token",
    "email_code_requested": "发送邮箱验证码",
    "email_code_received": "获取邮箱验证码",
    "email_code_verified": "确认邮箱验证码",
    "turnstile_token_ready": "完成人机验证",
    "final_anti_abuse_token_ready": "生成注册阶段 Castle Token",
    "create_session_rpc_completed": "提交账号注册请求",
    "session_token_ready": "获取 SSO 凭据",
}
STAGE_NUMBERS = {stage: index for index, stage in enumerate(STAGE_LABELS, start=1)}

_RESULT_LOCK = threading.Lock()


def load_config(path: str = "") -> dict:
    return load_env(path or None)


def missing_slots(config: dict) -> list[str]:
    checks = {
        "CAPSOLVER_API_KEY": config.get("capsolver_api_key"),
        "MOEMAIL_API_BASE": config.get("moemail_api_base"),
        "MOEMAIL_API_KEY": config.get("moemail_api_key"),
    }
    if not (
        config.get("castle_provider_url")
        or (config.get("castle_email_token") and config.get("castle_final_token"))
        or PROTOCOL_CASTLE_PUBLISHABLE_KEY
    ):
        checks["CASTLE_TOKEN_PROVIDER"] = ""
    return [name for name, value in checks.items() if not str(value or "").strip()]


def write_result_json(path: str | Path, result) -> Path:
    """将注册账号追加到 JSON 数组，并将 SSO 追加到同名 TXT。"""
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "email": str(result.email),
        "password": str(result.password),
        "sso": str(result.session_token),
    }
    with _RESULT_LOCK:
        records = []
        if output.exists():
            try:
                existing = json.loads(output.read_text(encoding="utf-8-sig"))
                if isinstance(existing, list):
                    records = existing
                elif isinstance(existing, dict):
                    records = [{key: existing[key] for key in ("created_at", "email", "password", "sso") if key in existing}]
            except (OSError, ValueError):
                records = []
        records.append(payload)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

        txt_output = output.with_suffix(".txt")
        token = payload["sso"].strip()
        existing_tokens = set(txt_output.read_text(encoding="utf-8-sig").splitlines()) if txt_output.exists() else set()
        if token and token not in existing_tokens:
            with txt_output.open("a", encoding="utf-8", newline="") as handle:
                handle.write(token + "\n")
    return output


def emit_event(enabled: bool, event: str, **fields) -> None:
    """Emit one flush-safe JSONL event without exposing credentials."""
    if enabled:
        print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def report_progress(events: bool, stage: str, task_id: int = 0) -> None:
    if events:
        emit_event(True, "progress", stage=stage, task=task_id)
        return
    label = STAGE_LABELS.get(stage, stage)
    step = STAGE_NUMBERS.get(stage, 0)
    step_prefix = f"[步骤 {step}/{len(STAGE_LABELS)}]" if step else "[步骤]"
    prefix = f"[任务 {task_id}]{step_prefix}" if task_id else step_prefix
    print(f"{prefix} {label}...", flush=True)


def run_web_task(
    task_id: int,
    config: dict,
    proxies,
    output_json: str,
    events: bool,
    *,
    save_result: bool = True,
):
    fingerprint = build_fingerprint(config)
    emit_event(events, "fingerprint", task=task_id, **fingerprint.public_metadata())
    base = PROTOCOL_TARGET_BASE
    page_url = str(config.get("protocol_page_url") or base + "/sign-up")
    client = AuthProtocolClient(base, proxies=proxies, user_agent=fingerprint.user_agent, default_headers=fingerprint.headers())
    flow = ProtocolRegistrationFlow(
        config=ProtocolRegistrationConfig(
            page_url=page_url,
            sitekey=PROTOCOL_TURNSTILE_SITEKEY,
            action=str(config.get("protocol_turnstile_action") or ""),
            tos_accepted_version=int(config.get("protocol_tos_accepted_version", 1) or 1),
        ),
        client=client,
        mail=MoeMailProvider(
            str(config["moemail_api_base"]),
            str(config["moemail_api_key"]),
            domain=str(config.get("moemail_domain") or ""),
            expiry_time=int(config.get("moemail_expiry_time", 86_400_000) or 86_400_000),
            proxies=proxies if config.get("moemail_use_proxy") else None,
        ),
        anti_abuse=(
            CastleTokenProvider(
                email_token=str(config.get("castle_email_token") or ""),
                final_token=str(config.get("castle_final_token") or ""),
                provider_url=str(config.get("castle_provider_url") or ""),
                provider_key=str(config.get("castle_provider_key") or ""),
            )
            if config.get("castle_provider_url") or config.get("castle_email_token") or config.get("castle_final_token")
            else CastleSdkTokenProvider(PROTOCOL_CASTLE_PUBLISHABLE_KEY, page_url, fingerprint.user_agent)
        ),
        human_verification=CapSolverProvider(
            str(config["capsolver_api_key"]),
            timeout=float(config.get("capsolver_timeout_sec", 120) or 120),
            poll_interval=float(config.get("capsolver_poll_interval_sec", 1.0) or 1.0),
            proxy=str(config.get("proxy") or "") if config.get("proxy_enabled", True) else "",
            user_agent=fingerprint.user_agent,
        ),
        on_progress=lambda stage: report_progress(events, stage, task_id),
    )
    result = flow.run()
    result.web_user_agent = fingerprint.user_agent
    output = write_result_json(output_json, result) if save_result else None
    return result, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Pure HTTP/gRPC-Web registration flow")
    parser.add_argument("--env", default=str(Path(__file__).resolve().parents[1] / ".env"))
    parser.add_argument("-n", "--count", type=int, default=1, help="注册数量")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="并发任务数")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--proxy-check", action="store_true")
    parser.add_argument(
        "--events",
        action="store_true",
        help="杈撳嚭鑴辨晱鍚庣殑 JSONL 杩涘害浜嬩欢",
    )
    parser.add_argument(
        "--output-json",
        default=str(Path(__file__).resolve().parents[1] / "output" / "web_register_result.json"),
        help="credential result JSON path (written atomically)",
    )
    args = parser.parse_args()
    if args.count < 1 or args.jobs < 1:
        parser.error("-n/--count 和 -j/--jobs 必须大于 0")
    args.jobs = min(args.jobs, args.count)
    config = load_config(args.env)
    proxy_enabled = bool(config.get("proxy_enabled", True))
    proxy = str(config.get("proxy") or "").strip() if proxy_enabled else ""
    proxies = None
    if proxy:
        normalized = normalize_proxy_url(proxy)
        proxies = {"http": normalized, "https": normalized}
        if not args.events:
            print(f"[网络] 使用代理: {redact_proxy_url(normalized)}", flush=True)
    elif not args.events:
        print("[网络] 使用直连", flush=True)

    fingerprint = build_fingerprint(config)

    if args.proxy_check:
        if not proxy:
            emit_event(args.events, "proxy_test", success=True, mode="direct", status=0)
            if not args.events:
                print(json.dumps({"success": True, "mode": "direct", "status": 0}))
            return 0
        import time
        from curl_cffi import requests

        started = time.monotonic()
        try:
            response = requests.get(
                str(config.get("protocol_page_url") or PROTOCOL_TARGET_BASE),
                proxies=proxies,
                headers=fingerprint.headers(),
                impersonate="chrome",
                timeout=15,
            )
            payload = {
                "success": 200 <= int(response.status_code) < 500,
                "mode": "proxy",
                "status": int(response.status_code),
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        except Exception as exc:
            payload = {
                "success": False,
                "mode": "proxy",
                "status": 0,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "error_type": type(exc).__name__,
            }
        emit_event(args.events, "proxy_test", **payload)
        if not args.events:
            print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["success"] else 1

    missing = missing_slots(config)
    if args.check or missing:
        payload = {"browser": False, "ready": not missing, "missing_slots": missing}
        if args.events:
            emit_event(True, "check", **payload)
        else:
            print(json.dumps(payload, ensure_ascii=False))
        return 0 if args.check else 2

    success = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_web_task, task_id, config, proxies, args.output_json, args.events): task_id for task_id in range(1, args.count + 1)}
        for future in as_completed(futures):
            task_id = futures[future]
            try:
                _, output = future.result()
                success += 1
                emit_event(args.events, "task_complete", task=task_id, success=True)
                if not args.events:
                    print(f"[任务 {task_id}] 注册成功", flush=True)
            except Exception as exc:
                emit_event(args.events, "task_complete", task=task_id, success=False, error_type=type(exc).__name__, message=str(exc))
                if not args.events:
                    print(f"[任务 {task_id}] 注册失败: {type(exc).__name__}: {exc}", flush=True)
    failed = args.count - success
    if args.events:
        emit_event(True, "batch_complete", total=args.count, success=success, failed=failed)
    else:
        output = Path(args.output_json).resolve()
        print(f"[完成] 总数={args.count} 成功={success} 失败={failed}", flush=True)
        print(f"[结果] JSON: {output}", flush=True)
        print(f"[结果] SSO TXT: {output.with_suffix('.txt')}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
