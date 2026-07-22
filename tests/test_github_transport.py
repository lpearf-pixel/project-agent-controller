from __future__ import annotations

import os

import httpx
import pytest

from project_agent_controller.observer.github_transport import (
    GitHubReadTransport,
    GitHubTransportError,
    resolve_credential,
    validate_github_request,
)


def test_resolve_credential_uses_env_only(monkeypatch) -> None:
    monkeypatch.setenv("PAC_TEST_TOKEN", "secret-token")
    assert resolve_credential("env://PAC_TEST_TOKEN") == "secret-token"
    assert resolve_credential(None) is None
    with pytest.raises(ValueError, match="env://"):
        resolve_credential("literal-secret")


def test_validate_request_is_get_only_and_path_limited() -> None:
    good = "/repos/owner/repo/commits/" + "a" * 40 + "/check-runs"
    validate_github_request("GET", good)
    with pytest.raises(GitHubTransportError, match="GET-only"):
        validate_github_request("POST", good)
    with pytest.raises(GitHubTransportError, match="not allowed"):
        validate_github_request("GET", "/repos/owner/repo/actions/runs/1/rerun")


def test_transport_sets_headers_and_supports_etag(monkeypatch) -> None:
    monkeypatch.setenv("PAC_TEST_TOKEN", "secret-token")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            headers={"ETag": '"abc"', "X-RateLimit-Remaining": "42"},
            json={"total_count": 0, "check_runs": []},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    transport = GitHubReadTransport(
        "https://api.github.com",
        api_version="2022-11-28",
        credential_ref="env://PAC_TEST_TOKEN",
        client=client,
    )
    path = "/repos/owner/repo/commits/" + "a" * 40 + "/check-runs"
    result = transport.get(path, params={"per_page": 100}, etag='"old"')

    headers = captured["headers"]
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["if-none-match"] == '"old"'
    assert result.etag == '"abc"'
    assert result.rate_limit_remaining == 42


def test_transport_returns_not_modified() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(304, headers={"ETag": '"x"'})),
        follow_redirects=False,
    )
    transport = GitHubReadTransport("https://api.github.com", client=client)
    path = "/repos/owner/repo/commits/" + "a" * 40 + "/status"
    result = transport.get(path, etag='"x"')
    assert result.not_modified is True
    assert result.data is None


def test_transport_rejects_large_body_and_hides_token(monkeypatch) -> None:
    monkeypatch.setenv("PAC_TEST_TOKEN", "top-secret")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, content=b"x" * 50)),
        follow_redirects=False,
    )
    transport = GitHubReadTransport(
        "https://api.github.com",
        credential_ref="env://PAC_TEST_TOKEN",
        max_response_bytes=16,
        client=client,
    )
    path = "/repos/owner/repo/commits/" + "a" * 40 + "/status"
    with pytest.raises(GitHubTransportError) as exc:
        transport.get(path)
    assert "top-secret" not in str(exc.value)
