from __future__ import annotations

from project_agent_controller.observer.github_ci_provider import GitHubCIProvider
from project_agent_controller.observer.github_transport import GitHubResponse


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, path, *, params=None, etag=None):
        self.calls.append((path, params, etag))
        return next(self.responses)


def response(data, *, etag=None, remaining=100, not_modified=False):
    return GitHubResponse(
        status_code=304 if not_modified else 200,
        data=None if not_modified else data,
        etag=etag,
        not_modified=not_modified,
        rate_limit_remaining=remaining,
        rate_limit_reset=None,
    )


def test_provider_normalizes_failure_and_truncates_summary() -> None:
    checks = {
        "total_count": 3,
        "check_runs": [
            {"id": 1, "name": "build", "status": "completed", "conclusion": "success"},
            {
                "id": 2,
                "name": "tests",
                "status": "completed",
                "conclusion": "failure",
                "details_url": "https://github.com/owner/repo/actions/runs/1",
                "output": {"summary": "错" * 600},
            },
            {"id": 3, "name": "lint", "status": "completed", "conclusion": "neutral"},
        ],
    }
    statuses = {"state": "success", "total_count": 1, "statuses": []}
    provider = GitHubCIProvider(
        Transport([response(checks, etag='"c"'), response(statuses, etag='"s"')])
    )

    snapshot = provider.snapshot(
        "owner/repo", "a" * 40, previous=None, max_check_runs=100, max_failed_checks=20
    )

    assert snapshot.overall == "failure"
    assert snapshot.total_checks == 4
    assert snapshot.failure_count == 1
    assert snapshot.success_count == 2
    assert len(snapshot.failed_checks) == 1
    assert len(snapshot.failed_checks[0].summary.encode("utf-8")) <= 512
    assert snapshot.etag_check_runs == '"c"'
    assert snapshot.etag_status == '"s"'


def test_provider_handles_pending_and_no_checks() -> None:
    pending_checks = {
        "total_count": 1,
        "check_runs": [{"id": 1, "name": "tests", "status": "in_progress", "conclusion": None}],
    }
    empty_status = {"state": "pending", "total_count": 0, "statuses": []}
    provider = GitHubCIProvider(Transport([response(pending_checks), response(empty_status)]))
    pending = provider.snapshot(
        "owner/repo", "b" * 40, previous=None, max_check_runs=100, max_failed_checks=20
    )
    assert pending.overall == "pending"

    provider = GitHubCIProvider(
        Transport(
            [
                response({"total_count": 0, "check_runs": []}),
                response({"state": "pending", "total_count": 0, "statuses": []}),
            ]
        )
    )
    empty = provider.snapshot(
        "owner/repo", "b" * 40, previous=None, max_check_runs=100, max_failed_checks=20
    )
    assert empty.overall == "no_checks"
    assert empty.total_checks == 0


def test_provider_uses_previous_endpoint_summary_on_304() -> None:
    previous = {
        "check_summary": {
            "total": 1,
            "success": 1,
            "pending": 0,
            "failure": 0,
            "cancelled": 0,
            "neutral": 0,
            "failed_checks": [],
        },
        "legacy_summary": {"state": "success", "total": 1},
        "etag_check_runs": '"old-c"',
        "etag_status": '"old-s"',
    }
    provider = GitHubCIProvider(
        Transport(
            [
                response(None, etag='"old-c"', not_modified=True),
                response(None, etag='"old-s"', not_modified=True),
            ]
        )
    )
    snapshot = provider.snapshot(
        "owner/repo", "c" * 40, previous=previous, max_check_runs=100, max_failed_checks=20
    )
    assert snapshot.overall == "success"
    assert snapshot.not_modified is True
    assert snapshot.total_checks == 2


def test_provider_limits_failed_checks() -> None:
    runs = [
        {
            "id": index,
            "name": f"check-{index}",
            "status": "completed",
            "conclusion": "failure",
            "output": {"summary": "failed"},
        }
        for index in range(5)
    ]
    provider = GitHubCIProvider(
        Transport(
            [
                response({"total_count": 5, "check_runs": runs}),
                response({"state": "success", "total_count": 0, "statuses": []}),
            ]
        )
    )
    snapshot = provider.snapshot(
        "owner/repo", "d" * 40, previous=None, max_check_runs=100, max_failed_checks=2
    )
    assert len(snapshot.failed_checks) == 2
    assert snapshot.failure_count == 5
