#!/usr/bin/env python3
"""Browser-free registration coordinator built on HTTP, mail APIs, and gRPC-Web."""

from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import string
from typing import Any, Callable, Protocol

from registration.protocol_client import (
    AuthProtocolClient,
    RpcResult,
    parse_message,
)
from providers.turnstile_flow import ChallengeContext


class MailProvider(Protocol):
    def create(self) -> tuple[str, str]: ...
    def wait_code(self, token: str, email: str) -> str: ...


class AntiAbuseProvider(Protocol):
    def acquire(self, *, stage: str, email: str) -> str: ...


class HumanVerificationProvider(Protocol):
    def acquire(self, challenge: ChallengeContext): ...


@dataclass(frozen=True)
class ProtocolRegistrationConfig:
    page_url: str
    sitekey: str
    action: str = ""
    tos_accepted_version: int = 1


@dataclass(frozen=True)
class Profile:
    given_name: str
    family_name: str
    password: str


@dataclass
class ProtocolRegistrationResult:
    success: bool
    state: str
    history: list[str] = field(default_factory=list)
    email: str = ""
    password: str = ""
    session_token: str = ""
    rpc: RpcResult | None = field(default=None, repr=False)


def generate_profile() -> Profile:
    given_names = ("Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo")
    family_names = ("Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun")
    alphabet = string.ascii_letters + string.digits
    password = "N!" + "".join(secrets.choice(alphabet) for _ in range(18)) + "#7"
    return Profile(secrets.choice(given_names), secrets.choice(family_names), password)


def _cookie_token(cookie_source: Any) -> str:
    if cookie_source is None:
        return ""
    for name in ("sso", "sso-rw"):
        try:
            value = cookie_source.get(name)
        except Exception:
            value = ""
        if value:
            return str(value)
    jar = getattr(cookie_source, "jar", cookie_source)
    try:
        iterable = list(jar)
    except Exception:
        iterable = []
    for cookie in iterable:
        name = str(getattr(cookie, "name", "") or "")
        value = str(getattr(cookie, "value", "") or "")
        if name in {"sso", "sso-rw"} and value:
            return value
    return ""


def session_token_from_rpc(result: Any) -> str:
    for jar in (
        getattr(getattr(result, "response", None), "cookies", None),
        getattr(getattr(result, "session", None), "cookies", None),
    ):
        token = _cookie_token(jar)
        if token:
            return token
    try:
        for name in ("sso", "sso-rw"):
            value = result.response.cookies.get(name)
            if value:
                return str(value)
    except Exception:
        pass
    messages = getattr(result, "messages", None) or []
    if not messages:
        return ""
    try:
        outer = {field.number: field.value for field in parse_message(messages[0])}
        # CreateSessionV2Response.session -> CreateSessionResponse.session_cookie.
        nested = outer.get(1)
        if isinstance(nested, bytes):
            inner = {field.number: field.value for field in parse_message(nested)}
            token = inner.get(2)
            if isinstance(token, bytes):
                return token.decode("utf-8", "replace")
    except Exception:
        return ""
    return ""


class ProtocolRegistrationFlow:
    """Execute the full registration state machine without a browser object."""

    def __init__(
        self,
        *,
        config: ProtocolRegistrationConfig,
        client: AuthProtocolClient,
        mail: MailProvider,
        anti_abuse: AntiAbuseProvider,
        human_verification: HumanVerificationProvider,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.client = client
        self.mail = mail
        self.anti_abuse = anti_abuse
        self.human_verification = human_verification
        self.on_progress = on_progress

    def _progress(self, stage: str, history: list[str]) -> None:
        history.append(stage)
        if self.on_progress is not None:
            self.on_progress(stage)

    def run(self) -> ProtocolRegistrationResult:
        history: list[str] = []
        self._progress("init", history)
        self.client.bootstrap(self.config.page_url)
        self._progress("protocol_session_bootstrapped", history)

        email, mail_token = self.mail.create()
        self._progress("email_created", history)

        email_castle_token = self.anti_abuse.acquire(stage="email", email=email)
        self._progress("email_anti_abuse_token_ready", history)
        self.client.create_email_validation_code(
            email,
            castle_request_token=email_castle_token,
        )
        self._progress("email_code_requested", history)

        code = str(self.mail.wait_code(mail_token, email) or "").replace("-", "").strip()
        if not code:
            raise RuntimeError("mail provider returned an empty verification code")
        self._progress("email_code_received", history)

        self.client.verify_email_validation_code(email, code)
        self._progress("email_code_verified", history)

        profile = generate_profile()

        challenge = ChallengeContext(
            page_url=self.config.page_url,
            sitekey=self.config.sitekey,
            action=self.config.action,
        )
        acquired = self.human_verification.acquire(challenge)
        turnstile_token = str(getattr(acquired, "value", acquired) or "").strip()
        if not turnstile_token:
            raise RuntimeError("human verification provider returned an empty token")
        self._progress("turnstile_token_ready", history)

        final_castle_token = self.anti_abuse.acquire(stage="final", email=email)
        self._progress("final_anti_abuse_token_ready", history)
        result = self.client.create_user_and_session(
            email=email,
            given_name=profile.given_name,
            family_name=profile.family_name,
            password=profile.password,
            email_validation_code=code,
            turnstile_token=turnstile_token,
            castle_request_token=final_castle_token,
            conversion_id=secrets.token_hex(16),
            tos_accepted_version=self.config.tos_accepted_version,
            use_v2=True,
        )
        self._progress("create_session_rpc_completed", history)
        session_token = session_token_from_rpc(result)
        if not session_token:
            raise RuntimeError("create-session response contained no session token")
        self._progress("session_token_ready", history)
        return ProtocolRegistrationResult(
            True,
            "completed",
            history,
            email=email,
            password=profile.password,
            session_token=session_token,
            rpc=result,
        )
