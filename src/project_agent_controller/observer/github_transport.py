from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


class GitHubTransportError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "transport_error",
        status_code: int | None = None,
        rate_limit_remaining: int | None = None,
        rate_limit_reset: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset


_ALLOWED_PATHS = (
    re.compile(r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commits/[0-9a-f]{40}/check-runs$"),
    re.compile(r"^/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commits/[0-9a-f]{40}/status$"),
)


def validate_github_request(method: str, path: str) -> None:
    if method.upper() != "GET":
        raise GitHubTransportError("GitHub transport is GET-only", kind="forbidden_method")
    clean = urlsplit(path).path
    if not any(pattern.fullmatch(clean) for pattern in _ALLOWED_PATHS):
        raise GitHubTransportError(
            f"GitHub GET path is not allowed: {clean}", kind="forbidden_path"
        )


def resolve_credential(ref: str | None) -> str | None:
    if ref is None:
        return None
    match = re.fullmatch(r"env://([A-Z][A-Z0-9_]+)", ref)
    if match is None:
        raise ValueError("credential_ref must use env://UPPER_CASE_NAME")
    return os.environ.get(match.group(1))


@dataclass(frozen=True, slots=True)
class GitHubResponse:
    status_code: int
    data: Any | None
    etag: str | None
    not_modified: bool
    rate_limit_remaining: int | None
    rate_limit_reset: int | None


class GitHubReadTransport:
    def __init__(
        self,
        api_base_url: str,
        *,
        api_version: str = "2022-11-28",
        credential_ref: str | None = None,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.api_base_url = api_base_url.rstrip("/")
        self.api_version = api_version
        self.credential_ref = credential_ref
        self.max_response_bytes = max_response_bytes
        self.client = client or httpx.Client(
            base_url=self.api_base_url,
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def get(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        etag: str | None = None,
    ) -> GitHubResponse:
        validate_github_request("GET", path)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "project-agent-controller/v0.1c",
        }
        token = resolve_credential(self.credential_ref)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if etag:
            headers["If-None-Match"] = etag
        try:
            response = self.client.get(f"{self.api_base_url}{path}", params=params, headers=headers)
        except httpx.TimeoutException as error:
            raise GitHubTransportError("GitHub request timed out", kind="timeout") from error
        except httpx.HTTPError as error:
            raise GitHubTransportError(
                f"GitHub endpoint unavailable: {type(error).__name__}", kind="network"
            ) from error

        remaining = self._int_header(response.headers.get("X-RateLimit-Remaining"))
        reset = self._int_header(response.headers.get("X-RateLimit-Reset"))
        response_etag = response.headers.get("ETag")
        if response.status_code == 304:
            return GitHubResponse(
                status_code=304,
                data=None,
                etag=response_etag or etag,
                not_modified=True,
                rate_limit_remaining=remaining,
                rate_limit_reset=reset,
            )
        body = response.content
        if len(body) > self.max_response_bytes:
            raise GitHubTransportError(
                f"GitHub response exceeds {self.max_response_bytes} bytes",
                kind="response_too_large",
                status_code=response.status_code,
                rate_limit_remaining=remaining,
                rate_limit_reset=reset,
            )
        if 300 <= response.status_code < 400:
            raise GitHubTransportError(
                f"GitHub redirect rejected ({response.status_code})",
                kind="redirect",
                status_code=response.status_code,
            )
        if response.status_code < 200 or response.status_code >= 300:
            kind = self._error_kind(response.status_code, remaining)
            raise GitHubTransportError(
                f"GitHub request failed ({response.status_code}, {kind})",
                kind=kind,
                status_code=response.status_code,
                rate_limit_remaining=remaining,
                rate_limit_reset=reset,
            )
        try:
            data = response.json()
        except ValueError as error:
            raise GitHubTransportError(
                "GitHub returned invalid JSON",
                kind="invalid_json",
                status_code=response.status_code,
            ) from error
        return GitHubResponse(
            status_code=response.status_code,
            data=data,
            etag=response_etag,
            not_modified=False,
            rate_limit_remaining=remaining,
            rate_limit_reset=reset,
        )

    @staticmethod
    def _int_header(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _error_kind(status_code: int, remaining: int | None) -> str:
        if status_code == 403 and remaining == 0:
            return "rate_limited"
        if status_code in {401, 403}:
            return "auth_failed"
        if status_code == 404:
            return "not_found"
        if status_code >= 500:
            return "server_error"
        return "http_error"
