from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from project_agent_controller.domain.models import EventRecord, GitSourceConfig, Severity
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.git_provider import GitSnapshot
from project_agent_controller.observer.git_transport import GitTransportError


class GitSnapshotProvider(Protocol):
    def snapshot(self, source: GitSourceConfig) -> GitSnapshot: ...


class GitSourceObserver:
    def __init__(self, provider: GitSnapshotProvider) -> None:
        self.provider = provider

    def observe(
        self,
        project_id: str,
        run_id: str,
        source: GitSourceConfig,
        previous: SourceState | None,
        *,
        now: datetime | None = None,
    ) -> SourceObservation:
        observed_at = now or datetime.now(UTC)
        previous_state = previous.state if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0
        events: list[EventRecord] = []
        incidents: list[tuple[str, EventRecord]] = []

        try:
            snapshot = self.provider.snapshot(source)
        except (GitTransportError, ValueError) as error:
            error_kind = type(error).__name__
            if (
                previous_state.get("available") is not False
                or previous_state.get("error_kind") != error_kind
            ):
                sequence += 1
                events.append(
                    self._event(
                        project_id,
                        run_id,
                        source.source_id,
                        sequence,
                        "git.provider.unavailable",
                        Severity.WARNING,
                        {"error_kind": error_kind},
                        observed_at,
                    )
                )
            state = {
                "available": False,
                "error_kind": error_kind,
                "last_heartbeat_at": previous_state.get(
                    "last_heartbeat_at", observed_at.isoformat()
                ),
            }
            return SourceObservation(
                events=tuple(events),
                state=SourceState(
                    project_id=project_id,
                    source_id=source.source_id,
                    source_kind="git",
                    sequence=sequence,
                    observed_at=observed_at,
                    state=state,
                ),
            )

        current = snapshot.model_dump(mode="json")
        current["available"] = True
        current["error_kind"] = None
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
                    "git.available",
                    Severity.INFO,
                    self._summary(current),
                    observed_at,
                )
            )
            last_heartbeat = observed_at
        elif not was_available:
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    "git.recovered",
                    Severity.INFO,
                    self._summary(current),
                    observed_at,
                )
            )
            last_heartbeat = observed_at
        else:
            transitions = self._transitions(previous_state, current)
            for event_type, severity, payload in transitions:
                sequence += 1
                event = self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    event_type,
                    severity,
                    payload,
                    observed_at,
                )
                events.append(event)
                if event_type == "git.conflict.entered":
                    material = (
                        f"{project_id}\0{source.source_id}\0{current.get('head_sha')}\0git.conflict"
                    ).encode()
                    incidents.append((f"fp-{sha256(material).hexdigest()[:24]}", event))
            if not transitions and (
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
                        "git.heartbeat",
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
                source_kind="git",
                sequence=sequence,
                observed_at=observed_at,
                state=current,
            ),
            incident_candidates=tuple(incidents),
        )

    @staticmethod
    def _transitions(
        previous: dict[str, object], current: dict[str, object]
    ) -> list[tuple[str, Severity, dict[str, object]]]:
        items: list[tuple[str, Severity, dict[str, object]]] = []
        if previous.get("head_sha") != current.get("head_sha"):
            items.append(
                (
                    "git.head.changed",
                    Severity.INFO,
                    {
                        "previous": previous.get("head_sha"),
                        "current": current.get("head_sha"),
                    },
                )
            )
        if previous.get("branch") != current.get("branch"):
            items.append(
                (
                    "git.branch.changed",
                    Severity.INFO,
                    {
                        "previous": previous.get("branch"),
                        "current": current.get("branch"),
                    },
                )
            )
        if bool(previous.get("detached")) != bool(current.get("detached")):
            items.append(
                (
                    "git.detached.entered" if current.get("detached") else "git.detached.cleared",
                    Severity.WARNING if current.get("detached") else Severity.INFO,
                    {},
                )
            )
        if bool(previous.get("dirty")) != bool(current.get("dirty")):
            items.append(
                (
                    ("git.dirty.entered" if current.get("dirty") else "git.dirty.cleared"),
                    Severity.WARNING if current.get("dirty") else Severity.INFO,
                    GitSourceObserver._counts(current),
                )
            )
        previous_conflicts = GitSourceObserver._int_value(previous.get("conflict_count"))
        current_conflicts = GitSourceObserver._int_value(current.get("conflict_count"))
        if (previous_conflicts == 0) != (current_conflicts == 0):
            items.append(
                (
                    "git.conflict.entered" if current_conflicts > 0 else "git.conflict.cleared",
                    Severity.ERROR if current_conflicts > 0 else Severity.INFO,
                    {
                        "conflict_count": current_conflicts,
                        "message": "git conflicts detected",
                    },
                )
            )
        if GitSourceObserver._int_value(previous.get("ahead")) != GitSourceObserver._int_value(
            current.get("ahead")
        ):
            items.append(
                (
                    "git.ahead.changed",
                    Severity.INFO,
                    {
                        "previous": previous.get("ahead"),
                        "current": current.get("ahead"),
                    },
                )
            )
        if GitSourceObserver._int_value(previous.get("behind")) != GitSourceObserver._int_value(
            current.get("behind")
        ):
            items.append(
                (
                    "git.behind.changed",
                    Severity.INFO,
                    {
                        "previous": previous.get("behind"),
                        "current": current.get("behind"),
                    },
                )
            )
        return items

    @staticmethod
    def _counts(state: dict[str, object]) -> dict[str, object]:
        return {
            "staged_count": state.get("staged_count", 0),
            "unstaged_count": state.get("unstaged_count", 0),
            "untracked_count": state.get("untracked_count", 0),
            "conflict_count": state.get("conflict_count", 0),
        }

    @staticmethod
    def _int_value(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _summary(state: dict[str, object]) -> dict[str, object]:
        return {
            "head_sha": state.get("head_sha"),
            "branch": state.get("branch"),
            "detached": state.get("detached", False),
            "ahead": state.get("ahead", 0),
            "behind": state.get("behind", 0),
            "dirty": state.get("dirty", False),
            "remote_tracking_only": True,
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
