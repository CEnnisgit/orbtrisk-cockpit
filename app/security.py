from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.settings import settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginRateLimiter:
    """Simple in-memory limiter keyed by client IP."""

    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_limited(self, key: str, *, max_attempts: int, window_seconds: int) -> bool:
        if not key:
            return False
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and (now - attempts[0]) > window_seconds:
                attempts.popleft()
            return len(attempts) >= max_attempts

    def record_failure(self, key: str, *, window_seconds: int) -> None:
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            attempts.append(now)
            while attempts and (now - attempts[0]) > window_seconds:
                attempts.popleft()

    def clear(self, key: str) -> None:
        if not key:
            return
        with self._lock:
            self._attempts.pop(key, None)


login_rate_limiter = LoginRateLimiter()


def safe_next_path(next_path: Optional[str], default: str = "/dashboard") -> str:
    candidate = (next_path or "").strip()
    if not candidate:
        return default
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    return candidate


def get_client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        xff = (request.headers.get("x-forwarded-for") or "").split(",")
        if xff and xff[0].strip():
            return xff[0].strip()
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def request_origin(request: Request) -> str:
    if settings.trust_proxy_headers:
        scheme = ((request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0]).strip()
        host = (
            (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0]
        ).strip()
        if scheme and host:
            return f"{scheme}://{host}".rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")


def is_same_origin(request: Request) -> bool:
    if request.method.upper() not in _UNSAFE_METHODS:
        return True

    sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site == "cross-site":
        return False

    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return True

    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    expected = request_origin(request)
    candidate = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if candidate == expected:
        return True

    return candidate in set(settings.allowed_origins_list)


def validate_webhook_target(url: str) -> str:
    candidate = (url or "").strip()
    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip()
    is_local_http = scheme == "http" and settings.webhook_allow_http_localhost and host in {"localhost", "127.0.0.1", "::1"}

    if not is_local_http and scheme not in settings.webhook_allowed_schemes_set:
        raise HTTPException(status_code=400, detail=f"Webhook URL scheme must be one of: {sorted(settings.webhook_allowed_schemes_set)}")

    if not host:
        raise HTTPException(status_code=400, detail="Webhook URL host is required")

    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Webhook URL must not include credentials")

    if settings.webhook_allow_private_targets:
        return candidate

    if is_local_http:
        return candidate

    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Webhook URL host could not be resolved") from exc

    for info in addr_info:
        ip_raw = info[4][0]
        ip = ipaddress.ip_address(ip_raw)
        if any(
            [
                ip.is_private,
                ip.is_loopback,
                ip.is_link_local,
                ip.is_reserved,
                ip.is_multicast,
                ip.is_unspecified,
            ]
        ):
            raise HTTPException(status_code=400, detail="Webhook URL must resolve to a public IP address")

    return candidate


def security_headers() -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }
    if settings.resolved_session_https_only:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
