#!/usr/bin/env python3
"""Proxy URL parsing and normalization for protocol requests."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote


@dataclass(frozen=True)
class ProxySpec:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def has_auth(self) -> bool:
        return bool(self.username or self.password)

    def as_url(self, include_auth: bool = True) -> str:
        auth = ""
        if include_auth and self.has_auth:
            auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


def _split_host_port(value: str) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or end + 2 > len(value) or value[end + 1] != ":":
            raise ValueError("IPv6 代理地址格式应为 [host]:port")
        host, port_text = value[1:end], value[end + 2 :]
    else:
        if ":" not in value:
            raise ValueError("代理地址缺少端口")
        host, port_text = value.rsplit(":", 1)
    if not host.strip() or not port_text.isdigit():
        raise ValueError("代理主机或端口格式错误")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("代理端口超出范围")
    return host.strip(), port


def parse_proxy_url(raw: str) -> ProxySpec:
    """Parse standard and common provider proxy formats.

    Accepted forms:
      - ``http://user:pass@host:port``
      - ``host:port:user:pass``
      - ``http://host:port@user:pass`` (provider/reversed form)
      - ``http://host:port``
    """
    value = str(raw or "").strip()
    if not value:
        raise ValueError("代理为空")
    if "://" in value:
        scheme, rest = value.split("://", 1)
    else:
        scheme, rest = "http", value
    scheme = scheme.lower().strip()
    if scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
        raise ValueError(f"浏览器代理暂只支持 HTTP/HTTPS，上游协议为 {scheme}")

    username = password = ""
    if "@" in rest:
        left, right = rest.rsplit("@", 1)
        # Some provider panels export host:port@username:password.
        left_is_host = False
        try:
            _split_host_port(left)
            left_is_host = True
        except ValueError:
            pass
        if left_is_host and ":" in right:
            host_port, credentials = left, right
        else:
            credentials, host_port = left, right
        if ":" not in credentials:
            raise ValueError("认证代理缺少 username:password")
        username, password = credentials.split(":", 1)
        host, port = _split_host_port(host_port)
    else:
        parts = rest.split(":", 3)
        if len(parts) == 4 and parts[1].isdigit():
            host, port = parts[0].strip(), int(parts[1])
            username, password = parts[2], parts[3]
            if not host or not 1 <= port <= 65535:
                raise ValueError("代理主机或端口格式错误")
        else:
            host, port = _split_host_port(rest)

    return ProxySpec(
        scheme=scheme,
        host=host,
        port=port,
        username=unquote(username),
        password=unquote(password),
    )


def normalize_proxy_url(raw: str) -> str:
    spec = parse_proxy_url(raw)
    # SOCKS5 默认在本机解析域名，可能命中污染或错误的 CDN 节点。
    # 使用 socks5h 将域名解析交给代理出口，避免 HTTPS 证书域名不匹配。
    if spec.scheme == "socks5":
        spec = ProxySpec(
            scheme="socks5h",
            host=spec.host,
            port=spec.port,
            username=spec.username,
            password=spec.password,
        )
    return spec.as_url(include_auth=True)


def redact_proxy_url(raw: str) -> str:
    spec = parse_proxy_url(raw)
    if not spec.has_auth:
        return spec.as_url(False)
    return f"{spec.scheme}://{spec.username[:3]}***:***@{spec.host}:{spec.port}"
