from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.observer.github_transport import GitHubResponse

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FAILURE_CONCLUSIONS = {
    "failure",
    "timed_out",
    "action_required",
    "startup_failure",
    "stale",
}
_NEUTRAL_CONCLUSIONS = {"neutral", "skipped"}


class GitHubTransport(Protocol):
    def get(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        etag: str | None = None,
    ) -> GitHubResponse: ...


class FailedCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    conclusion: str
    details_url: str | None = None
    summary: str = ""
    provider_object_id: str


class CISnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    head_sha: str
    overall: str
    total_checks: int = Field(ge=0)
    success_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    neutral_count: int = Field(ge=0)
    failed_checks: tuple[FailedCheck, ...] = ()
    legacy_status_state: str | None = None
    check_summary: dict[str, Any]
    legacy_summary: dict[str, Any]
    etag_check_runs: str | None = None
    etag_status: str | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None
    not_modified: bool = False


class GitHubCIProvider:
    def __init__(self, transport: GitHubTransport) -> None:
        self.transport = transport

    def snapshot(
        self,
        repository: str,
        sha: str,
        *,
        previous: dict[str, Any] | None,
        max_check_runs: int,
        max_failed_checks: int,
    ) -> CISnapshot:
        if _REPOSITORY.fullmatch(repository) is None:
            raise ValueError("repository must use owner/name")
        if _SHA.fullmatch(sha) is None:
            raise ValueError("sha must be 40 lowercase hexadecimal characters")
        previous = previous or {}
        check_response = self.transport.get(
            f"/repos/{repository}/commits/{sha}/check-runs",
            params={"per_page": max_check_runs, "filter": "latest"},
            etag=self._optional_str(previous.get("etag_check_runs")),
        )
        status_response = self.transport.get(
            f"/repos/{repository}/commits/{sha}/status",
            params={"per_page": 100},
            etag=self._optional_str(previous.get("etag_status")),
        )

        if check_response.not_modified:
            check_summary = self._dict(previous.get("check_summary"), "check_summary")
        else:
            check_summary = self._normalize_checks(check_response.data, max_failed_checks)
        if status_response.not_modified:
            legacy_summary = self._dict(previous.get("legacy_summary"), "legacy_summary")
        else:
            legacy_summary = self._normalize_legacy(status_response.data)

        counts = {
            "success": int(check_summary["success"]),
            "pending": int(check_summary["pending"]),
            "failure": int(check_summary["failure"]),
            "cancelled": int(check_summary["cancelled"]),
            "neutral": int(check_summary["neutral"]),
        }
        legacy_total = int(legacy_summary["total"])
        legacy_state = str(legacy_summary["state"]) if legacy_total > 0 else None
        if legacy_total > 0:
            if legacy_state in {"failure", "error"}:
                counts["failure"] += legacy_total
            elif legacy_state == "pending":
                counts["pending"] += legacy_total
            elif legacy_state == "success":
                counts["success"] += legacy_total

        total = int(check_summary["total"]) + legacy_total
        overall = self._overall(total, counts)
        remaining_values = [
            value
            for value in (
                check_response.rate_limit_remaining,
                status_response.rate_limit_remaining,
            )
            if value is not None
        ]
        reset_values = [
            value
            for value in (
                check_response.rate_limit_reset,
                status_response.rate_limit_reset,
            )
            if value is not None
        ]
        return CISnapshot(
            head_sha=sha,
            overall=overall,
            total_checks=total,
            success_count=counts["success"],
            pending_count=counts["pending"],
            failure_count=counts["failure"],
            cancelled_count=counts["cancelled"],
            neutral_count=counts["neutral"],
            failed_checks=tuple(
                FailedCheck.model_validate(item) for item in check_summary["failed_checks"]
            ),
            legacy_status_state=legacy_state,
            check_summary=check_summary,
            legacy_summary=legacy_summary,
            etag_check_runs=check_response.etag
            or self._optional_str(previous.get("etag_check_runs")),
            etag_status=status_response.etag
            or self._optional_str(previous.get("etag_status")),
            rate_limit_remaining=min(remaining_values) if remaining_values else None,
            rate_limit_reset=max(reset_values) if reset_values else None,
            not_modified=check_response.not_modified and status_response.not_modified,
        )

    @classmethod
    def _normalize_checks(cls, data: Any, max_failed_checks: int) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("check-runs response must be an object")
        runs = data.get("check_runs", [])
        if not isinstance(runs, list):
            raise ValueError("check_runs must be a list")
        counts = {"success": 0, "pending": 0, "failure": 0, "cancelled": 0, "neutral": 0}
        failed: list[dict[str, Any]] = []
        for raw in runs:
            if not isinstance(raw, dict):
                continue
            status = str(raw.get("status") or "")
            conclusion = raw.get("conclusion")
            conclusion_text = str(conclusion) if conclusion is not None else ""
            if status in {"queued", "in_progress", "pending"} or not conclusion_text:
                counts["pending"] += 1
            elif conclusion_text in _FAILURE_CONCLUSIONS:
                counts["failure"] += 1
                if len(failed) < max_failed_checks:
                    output = (
                        raw.get("output")
                        if isinstance(raw.get("output"), dict)
                        else {}
                    )
                    failed.append(
                        {
                            "name": str(raw.get("name") or "unnamed-check")[:200],
                            "conclusion": conclusion_text,
                            "details_url": cls._optional_str(raw.get("details_url")),
                            "summary": cls._truncate_utf8(
                                str(output.get("summary") or ""), 512
                            ),
                            "provider_object_id": str(raw.get("id") or "unknown"),
                        }
                    )
            elif conclusion_text == "cancelled":
                counts["cancelled"] += 1
            elif conclusion_text in _NEUTRAL_CONCLUSIONS:
                counts["neutral"] += 1
            elif conclusion_text == "success":
                counts["success"] += 1
            else:
                counts["neutral"] += 1
        return {
            "total": len(runs),
            **counts,
            "failed_checks": failed,
        }

    @staticmethod
    def _normalize_legacy(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("combined status response must be an object")
        total = data.get("total_count", 0)
        state = data.get("state", "pending")
        return {"total": int(total), "state": str(state)}

    @staticmethod
    def _overall(total: int, counts: dict[str, int]) -> str:
        if total == 0:
            return "no_checks"
        if counts["failure"] > 0:
            return "failure"
        if counts["cancelled"] > 0:
            return "cancelled"
        if counts["pending"] > 0:
            return "pending"
        if counts["success"] > 0:
            return "success"
        return "neutral"

    @staticmethod
    def _truncate_utf8(value: str, max_bytes: int) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= max_bytes:
            return value
        return encoded[:max_bytes].decode("utf-8", errors="ignore")

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _dict(value: object, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{name} missing for 304 response")
        return dict(value)


class UnavailableCIProvider:
    def __init__(self, reason: str = "SCM provider is unavailable") -> None:
        self.reason = reason

    def snapshot(
        self,
        repository: str,
        sha: str,
        *,
        previous: dict[str, Any] | None,
        max_check_runs: int,
        max_failed_checks: int,
    ) -> CISnapshot:
        del repository, sha, previous, max_check_runs, max_failed_checks
        from project_agent_controller.observer.github_transport import GitHubTransportError

        raise GitHubTransportError(self.reason, kind="provider_unavailable")
