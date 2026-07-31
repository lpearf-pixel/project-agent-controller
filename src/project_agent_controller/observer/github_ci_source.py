from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4

from project_agent_controller.domain.models import EventRecord, GitHubCISourceConfig, Severity
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.github_ci_provider import CISnapshot
from project_agent_controller.observer.github_transport import GitHubTransportError


class CIProvider(Protocol):
    def snapshot(
        self,
        repository: str,
        sha: str,
        *,
        previous: dict[str, Any] | None,
        max_check_runs: int,
        max_failed_checks: int,
    ) -> CISnapshot: ...


class GitHubCISourceObserver:
    def __init__(self, provider: CIProvider) -> None:
        self.provider = provider

    def observe(
        self,
        project_id: str,
        run_id: str,
        source: GitHubCISourceConfig,
        git_state: SourceState | None,
        previous: SourceState | None,
        *,
        now: datetime | None = None,
    ) -> SourceObservation:
        observed_at = now or datetime.now(UTC)
        previous_state = dict(previous.state) if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0

        head_sha = None
        if git_state is not None and git_state.state.get("available") is True:
            value = git_state.state.get("head_sha")
            if isinstance(value, str) and len(value) == 40:
                head_sha = value
        if head_sha is None:
            events: list[EventRecord] = []
            reason = "missing_git_state"
            if previous_state.get("blocked_reason") != reason:
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        "ci.blocked.missing_git_state",
                        Severity.WARNING,
                        {"git_source_id": source.git_source_id},
                        observed_at,
                    )
                )
            state = dict(previous_state)
            state.update(
                {
                    "available": False,
                    "blocked_reason": reason,
                    "error_kind": None,
                    "backoff_until": None,
                    "last_heartbeat_at": state.get("last_heartbeat_at", observed_at.isoformat()),
                }
            )
            return SourceObservation(
                events=tuple(events),
                state=SourceState(
                    project_id=project_id,
                    source_id=source.source_id,
                    source_kind="github_ci",
                    sequence=sequence,
                    observed_at=observed_at,
                    state=state,
                ),
            )

        backoff_until = self._parse_time(previous_state.get("backoff_until"))
        if backoff_until is not None and observed_at < backoff_until:
            return SourceObservation(
                events=(),
                state=SourceState(
                    project_id=project_id,
                    source_id=source.source_id,
                    source_kind="github_ci",
                    sequence=sequence,
                    observed_at=observed_at,
                    state=previous_state,
                ),
            )

        try:
            snapshot = self.provider.snapshot(
                source.repository,
                head_sha,
                previous=previous_state or None,
                max_check_runs=source.max_check_runs,
                max_failed_checks=source.max_failed_checks,
            )
        except GitHubTransportError as error:
            event_type = {
                "rate_limited": "ci.rate_limited",
                "auth_failed": "ci.auth.failed",
            }.get(error.kind, "ci.unavailable")
            events = []
            if (
                previous_state.get("available") is not False
                or previous_state.get("error_kind") != error.kind
            ):
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        event_type,
                        Severity.WARNING,
                        {
                            "provider_id": source.provider_id,
                            "repository": source.repository,
                            "error_kind": error.kind,
                        },
                        observed_at,
                    )
                )
            attempt = (
                int(previous_state.get("backoff_attempt") or 0) + 1
                if previous_state.get("error_kind") == error.kind
                else 1
            )
            until = self._backoff_until(observed_at, error, attempt)
            state = dict(previous_state)
            state.update(
                {
                    "available": False,
                    "blocked_reason": None,
                    "error_kind": error.kind,
                    "backoff_attempt": attempt,
                    "backoff_until": until.isoformat(),
                    "head_sha": head_sha,
                    "last_heartbeat_at": state.get("last_heartbeat_at", observed_at.isoformat()),
                }
            )
            return SourceObservation(
                events=tuple(events),
                state=SourceState(
                    project_id=project_id,
                    source_id=source.source_id,
                    source_kind="github_ci",
                    sequence=sequence,
                    observed_at=observed_at,
                    state=state,
                ),
            )

        current = snapshot.model_dump(mode="json")
        current.update(
            {
                "available": True,
                "blocked_reason": None,
                "error_kind": None,
                "backoff_attempt": 0,
                "backoff_until": None,
                "provider_id": source.provider_id,
                "repository": source.repository,
            }
        )
        failed_identities = self._failed_identities(snapshot)
        current["failed_identities"] = sorted(failed_identities)
        events = []
        incidents: list[tuple[str, EventRecord]] = []
        was_available = previous_state.get("available") is True
        last_heartbeat = self._parse_time(previous_state.get("last_heartbeat_at"))

        if previous is None:
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    "ci.available",
                    Severity.INFO,
                    self._summary(current),
                    observed_at,
                )
            )
        elif not was_available:
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    "ci.recovered",
                    Severity.INFO,
                    self._summary(current),
                    observed_at,
                )
            )
        else:
            previous_head = previous_state.get("head_sha")
            if previous_head != head_sha:
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        "ci.head.changed",
                        Severity.INFO,
                        {"previous": previous_head, "current": head_sha},
                        observed_at,
                    )
                )
            if previous_state.get("overall") != current.get("overall"):
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        "ci.status.changed",
                        Severity.WARNING
                        if current.get("overall") in {"failure", "cancelled"}
                        else Severity.INFO,
                        {
                            "previous": previous_state.get("overall"),
                            "current": current.get("overall"),
                            "head_sha": head_sha,
                        },
                        observed_at,
                    )
                )
            previous_ids = {str(item) for item in previous_state.get("failed_identities", [])}
            for check in snapshot.failed_checks:
                identity = self._identity(snapshot.head_sha, check.name, check.conclusion)
                if identity in previous_ids:
                    continue
                sequence += 1
                event = self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    "ci.check.failed",
                    Severity.ERROR,
                    {
                        "provider_id": source.provider_id,
                        "repository": source.repository,
                        "head_sha": head_sha,
                        "name": check.name,
                        "conclusion": check.conclusion,
                        "details_url": check.details_url,
                        "summary": check.summary,
                        "message": f"CI check failed: {check.name}",
                    },
                    observed_at,
                )
                events.append(event)
                material = (
                    f"{source.provider_id}\0{source.repository}\0{head_sha}\0"
                    f"{check.name}\0{check.conclusion}"
                ).encode()
                incidents.append((f"fp-{sha256(material).hexdigest()[:24]}", event))
            if previous_head == head_sha:
                recovered = previous_ids - failed_identities
                for identity in sorted(recovered):
                    sequence += 1
                    events.append(
                        self._event(
                            project_id,
                            run_id,
                            source.source_id,
                            sequence,
                            "ci.check.recovered",
                            Severity.INFO,
                            {"identity": identity, "head_sha": head_sha},
                            observed_at,
                        )
                    )
            if (
                current.get("overall") == "no_checks"
                and previous_state.get("overall") != "no_checks"
            ):
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        "ci.no_checks",
                        Severity.INFO,
                        {"head_sha": head_sha},
                        observed_at,
                    )
                )

        if events:
            last_heartbeat = observed_at
        elif (
            last_heartbeat is None
            or (observed_at - last_heartbeat).total_seconds() >= source.heartbeat_seconds
        ):
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    "ci.heartbeat",
                    Severity.INFO,
                    self._summary(current),
                    observed_at,
                )
            )
            last_heartbeat = observed_at

        current["last_heartbeat_at"] = (last_heartbeat or observed_at).isoformat()
        return SourceObservation(
            events=tuple(events),
            state=SourceState(
                project_id=project_id,
                source_id=source.source_id,
                source_kind="github_ci",
                sequence=sequence,
                observed_at=observed_at,
                state=current,
            ),
            incident_candidates=tuple(incidents),
        )

    @staticmethod
    def _failed_identities(snapshot: CISnapshot) -> set[str]:
        return {
            GitHubCISourceObserver._identity(snapshot.head_sha, check.name, check.conclusion)
            for check in snapshot.failed_checks
        }

    @staticmethod
    def _identity(head_sha: str, name: str, conclusion: str) -> str:
        return f"{head_sha}:{name}:{conclusion}"

    @staticmethod
    def _backoff_until(now: datetime, error: GitHubTransportError, attempt: int) -> datetime:
        if error.kind == "rate_limited" and error.rate_limit_reset is not None:
            reset = datetime.fromtimestamp(error.rate_limit_reset, tz=UTC)
            if reset > now:
                return reset
        if error.kind == "auth_failed":
            return now + timedelta(seconds=300)
        seconds = min(60 * (2 ** max(0, attempt - 1)), 900)
        return now + timedelta(seconds=seconds)

    @staticmethod
    def _summary(state: dict[str, Any]) -> dict[str, object]:
        return {
            "head_sha": state.get("head_sha"),
            "overall": state.get("overall"),
            "total_checks": state.get("total_checks", 0),
            "failure_count": state.get("failure_count", 0),
            "pending_count": state.get("pending_count", 0),
        }

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _event(
        project_id: str,
        run_id: str,
        source_id: str,
        sequence: int,
        event_type: str,
        severity: Severity,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> EventRecord:
        material = repr((event_type, sorted(payload.items()))).encode("utf-8")
        return EventRecord(
            event_id=f"evt-{uuid4()}",
            project_id=project_id,
            run_id=run_id,
            source_id=source_id,
            sequence=sequence,
            event_type=event_type,
            severity=severity,
            occurred_at=occurred_at,
            payload=payload,
            evidence_ref=f"artifact://sha256/{sha256(material).hexdigest()}",
        )
