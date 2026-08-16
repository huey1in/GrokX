#!/usr/bin/env python3
"""Minimal gRPC-Web protocol client reconstructed from the captured fixture schema."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urljoin

from curl_cffi import requests


SERVICE = "/auth_mgmt.AuthManagement"


class ProtocolError(RuntimeError):
    pass


def encode_varint(value: int) -> bytes:
    value = int(value)
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 70:
            break
    raise ProtocolError("invalid protobuf varint")


def field_varint(number: int, value: int | bool, *, omit_default: bool = True) -> bytes:
    value = int(value)
    if omit_default and value == 0:
        return b""
    return encode_varint((int(number) << 3) | 0) + encode_varint(value)


def field_bytes(number: int, value: bytes | str) -> bytes:
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not value:
        return b""
    return encode_varint((int(number) << 3) | 2) + encode_varint(len(value)) + value


def frame_message(message: bytes, *, trailer: bool = False) -> bytes:
    return bytes([0x80 if trailer else 0x00]) + len(message).to_bytes(4, "big") + message


@dataclass(frozen=True)
class ProtoField:
    number: int
    wire_type: int
    value: int | bytes


def parse_message(data: bytes) -> list[ProtoField]:
    fields = []
    offset = 0
    while offset < len(data):
        key, offset = decode_varint(data, offset)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, offset = decode_varint(data, offset)
        elif wire_type == 2:
            length, offset = decode_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ProtocolError("truncated protobuf field")
            value = data[offset:end]
            offset = end
        else:
            raise ProtocolError(f"unsupported protobuf wire type: {wire_type}")
        fields.append(ProtoField(number, wire_type, value))
    return fields


@dataclass(frozen=True)
class GrpcWebFrame:
    flag: int
    payload: bytes

    @property
    def is_trailer(self) -> bool:
        return bool(self.flag & 0x80)


def parse_frames(data: bytes) -> list[GrpcWebFrame]:
    frames = []
    offset = 0
    while offset < len(data):
        if offset + 5 > len(data):
            raise ProtocolError("truncated gRPC-Web frame header")
        flag = data[offset]
        length = int.from_bytes(data[offset + 1 : offset + 5], "big")
        offset += 5
        end = offset + length
        if end > len(data):
            raise ProtocolError("truncated gRPC-Web frame body")
        frames.append(GrpcWebFrame(flag, data[offset:end]))
        offset = end
    return frames


def parse_trailers(payload: bytes) -> dict[str, str]:
    out = {}
    for line in payload.decode("ascii", "replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


@dataclass
class RpcResult:
    method: str
    status_code: int
    messages: list[bytes]
    trailers: dict[str, str]
    response: Any


class AuthProtocolClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: Any = None,
        proxies: dict[str, str] | None = None,
        user_agent: str = "",
        default_headers: dict[str, str] | None = None,
        timeout: float = 30,
        attempts: int = 3,
    ):
        self.base_url = str(base_url or "").rstrip("/") + "/"
        self.session = session or requests.Session(impersonate="chrome", proxies=proxies or {})
        self.user_agent = str(user_agent or "")
        self.default_headers = {
            str(key).lower(): str(value)
            for key, value in (default_headers or {}).items()
            if str(value or "").strip()
        }
        self.timeout = max(1.0, float(timeout))
        self.attempts = max(1, int(attempts))

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        return getattr(self.session, method.lower())(url, **kwargs)

    def bootstrap(self, page_url: str) -> Any:
        headers = dict(self.default_headers)
        headers.update(
            {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "none",
            }
        )
        if self.user_agent:
            headers["user-agent"] = self.user_agent
        response = self._request("GET", page_url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response

    def _post(self, method: str, message: bytes) -> RpcResult:
        url = urljoin(self.base_url, f"{SERVICE}/{method}".lstrip("/"))
        origin = self.base_url.rstrip("/")
        headers = {
            "content-type": "application/grpc-web+proto",
            "accept": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": origin,
            "referer": origin + "/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        headers.update(self.default_headers)
        if self.user_agent:
            headers["user-agent"] = self.user_agent
        last_error = None
        response = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self._request(
                    "POST",
                    url,
                    data=frame_message(message),
                    headers=headers,
                    timeout=self.timeout,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt < self.attempts:
                    time.sleep(min(2 ** (attempt - 1), 4))
        if response is None:
            raise ProtocolError(f"RPC transport failed after {self.attempts} attempts: {last_error}")
        response.raise_for_status()
        frames = parse_frames(response.content)
        messages = [frame.payload for frame in frames if not frame.is_trailer]
        trailers = {}
        for frame in frames:
            if frame.is_trailer:
                trailers.update(parse_trailers(frame.payload))
        grpc_status = trailers.get("grpc-status", response.headers.get("grpc-status", "0"))
        if str(grpc_status) != "0":
            detail = trailers.get("grpc-message", "")
            raise ProtocolError(f"gRPC status {grpc_status}: {detail}")
        return RpcResult(method, response.status_code, messages, trailers, response)

    def create_email_validation_code(
        self,
        email: str,
        *,
        castle_request_token: str,
        email_template: int = 0,
    ) -> RpcResult:
        message = b"".join(
            (
                field_bytes(1, email),
                field_varint(2, email_template),
                field_bytes(3, castle_request_token),
            )
        )
        return self._post("CreateEmailValidationCode", message)

    def verify_email_validation_code(
        self,
        email: str,
        code: str,
        *,
        delete_on_success: bool = False,
        return_verification_token: bool = False,
    ) -> RpcResult:
        message = b"".join(
            (
                field_bytes(1, email),
                field_bytes(2, code),
                field_varint(3, delete_on_success),
                field_varint(4, return_verification_token),
            )
        )
        return self._post("VerifyEmailValidationCode", message)

    def create_user_and_session(
        self,
        *,
        email: str,
        given_name: str,
        family_name: str,
        password: str,
        email_validation_code: str,
        turnstile_token: str,
        castle_request_token: str,
        conversion_id: str = "",
        tos_accepted_version: int = 0,
        use_v2: bool = True,
    ) -> RpcResult:
        anti_abuse = field_bytes(1, turnstile_token)
        create_user = b"".join(
            (
                field_bytes(1, given_name),
                field_bytes(2, family_name),
                field_bytes(3, email),
                field_bytes(5, password),
                field_varint(6, tos_accepted_version),
                field_bytes(7, anti_abuse),
            )
        )
        request = b"".join(
            (
                field_bytes(1, create_user),
                field_bytes(6, anti_abuse),
                field_bytes(8, conversion_id),
                field_bytes(9, email_validation_code),
                field_bytes(11, castle_request_token),
            )
        )
        method = "CreateUserAndSessionV2" if use_v2 else "CreateUserAndSession"
        return self._post(method, request)
