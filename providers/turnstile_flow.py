#!/usr/bin/env python3
"""Turnstile challenge context and acquired-token model."""

from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass(frozen=True)
class ChallengeContext:
    page_url: str
    sitekey: str
    action: str = ""
    cdata: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AcquiredToken:
    value: str
    acquired_at: float = field(default_factory=time.time)
    source: str = "provider"
