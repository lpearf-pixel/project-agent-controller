from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from project_agent_controller.curation.fingerprint import fingerprint_event
from project_agent_controller.domain.models import DockerSourceConfig, EventRecord, Severity
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.docker_provider import (
    DockerLogCursor,
    DockerProvider,
    DockerSelectorAmbiguous,
    DockerSnapshot,
)
from project_agent_controller.observer.docker_transport import DockerTransportError


class DockerSourceObserver:
    def __init__(
        self,
        provider: DockerProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(UTC))

    def observe(
        self,
        project_id: str,
        run_id: str,
        source: DockerSourceConfig,
        previous: SourceState | None,
    ) -> SourceObservation:
        now = self.clock()
        try:
            container = self.provider.find_container(source.selector)
        except DockerSelectorAmbiguous:
            return self._unavailable(
                project_id,
                run_id,
                source,
                previous,
                now,
                "ambiguous",
                "docker.selector.ambiguous",
            )
        except DockerTransportError:
            return self._unavailable(
                project_id,
                run_id,
                source,
                previous,
                now,
                "provider_unavailable",
                "docker.provider.unavailable",
            )
        if container is None:
            return self._unavailable(
                project_id,
                run_id,
                source,
                previous,
                now,
                "missing",
                "docker.container.missing",
            )
        try:
            snapshot = self.provider.inspect(container.container_id)
        except DockerTransportError:
            return self._unavailable(
                project_id,
                run_id,
                source,
                previous,
                now,
                "inspect_unavailable",
                "docker.provider.unavailable",
            )
        return self._available(project_id, run_id, source, previous, now, snapshot)

    def _unavailable(
        self,
        project_id: str,
        run_id: str,
        source: DockerSourceConfig,
        previous: SourceState | None,
        now: datetime,
        error_kind: str,
        event_type: str,
    ) -> SourceObservation:
        prior = previous.state if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0
        events: list[EventRecord] = []
        if prior.get("presence") != "unavailable" or prior.get("error_kind") != error_kind:
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    event_type,
                    Severity.WARNING,
                    {"error_kind": error_kind},
                    f"docker://selector/{error_kind}",
                    now,
                )
            )
        return SourceObservation(
            events=tuple(events),
            state=SourceState(
                project_id=project_id,
                source_id=source.source_id,
                source_kind="docker",
                sequence=sequence,
                observed_at=now,
                state={
                    "presence": "unavailable",
                    "error_kind": error_kind,
                    "last_heartbeat_at": prior.get("last_heartbeat_at") or now.isoformat(),
                },
            ),
        )

    def _available(
        self,
        project_id: str,
        run_id: str,
        source: DockerSourceConfig,
        previous: SourceState | None,
        now: datetime,
        snapshot: DockerSnapshot,
    ) -> SourceObservation:
        prior = previous.state if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0
        events: list[EventRecord] = []
        incident_candidates: list[tuple[str, EventRecord]] = []

        def add(
            event_type: str,
            severity: Severity,
            payload: dict[str, object],
        ) -> EventRecord:
            nonlocal sequence
            sequence += 1
            event = self._event(
                project_id,
                run_id,
                source.source_id,
                sequence,
                event_type,
                severity,
                payload,
                f"docker://{snapshot.container_id}",
                now,
            )
            events.append(event)
            return event

        previously_available = prior.get("presence") == "available"
        same_container = previously_available and prior.get("container_id") == snapshot.container_id
        if not previously_available:
            add(
                "docker.container.available",
                Severity.INFO,
                {
                    "container_id": snapshot.container_id,
                    "name": snapshot.name,
                    "image": snapshot.image,
                },
            )
        elif not same_container or int(prior.get("restart_count", 0)) < snapshot.restart_count:
            add(
                "docker.container.restarted",
                Severity.WARNING,
                {
                    "previous_container_id": prior.get("container_id"),
                    "container_id": snapshot.container_id,
                    "previous_restart_count": prior.get("restart_count", 0),
                    "restart_count": snapshot.restart_count,
                },
            )
        if same_container and prior.get("state") != snapshot.state:
            add(
                "docker.state.changed",
                Severity.INFO,
                {"previous": prior.get("state"), "current": snapshot.state},
            )
        if same_container and prior.get("health") != snapshot.health:
            add(
                "docker.health.changed",
                Severity.WARNING if snapshot.health == "unhealthy" else Severity.INFO,
                {"previous": prior.get("health"), "current": snapshot.health},
            )
        if snapshot.state == "exited" and prior.get("state") != "exited":
            add(
                "docker.container.exited",
                Severity.ERROR if snapshot.exit_code != 0 else Severity.WARNING,
                {"exit_code": snapshot.exit_code},
            )
        if snapshot.oom_killed and not bool(prior.get("oom_killed", False)):
            add(
                "docker.container.oom_killed",
                Severity.CRITICAL,
                {"exit_code": snapshot.exit_code},
            )

        previous_memory_high = bool(prior.get("memory_high", False))
        memory_high = self._high(
            source.memory_warning_bytes,
            snapshot.memory_bytes,
            previous_memory_high,
        )
        if memory_high and not previous_memory_high:
            add(
                "docker.resource.threshold",
                Severity.WARNING,
                {"memory_bytes": snapshot.memory_bytes, "crossed": ["memory"]},
            )
        if previous_memory_high and not memory_high:
            add(
                "docker.resource.recovered",
                Severity.INFO,
                {"memory_bytes": snapshot.memory_bytes, "recovered": ["memory"]},
            )

        log_cursor = self._previous_log_cursor(prior, snapshot.container_id)
        next_log_cursor = log_cursor
        if source.include_logs:
            try:
                batch = self.provider.logs(
                    snapshot.container_id,
                    log_cursor,
                    limit=source.log_max_lines_per_cycle,
                    tail=source.log_tail_on_attach,
                )
                next_log_cursor = batch.cursor
                for line in batch.lines:
                    severity = self._classify_severity(line.line)
                    sequence += 1
                    event = EventRecord(
                        event_id=f"evt-{uuid4()}",
                        project_id=project_id,
                        run_id=run_id,
                        source_id=source.source_id,
                        sequence=sequence,
                        event_type="docker.log.line",
                        severity=severity,
                        occurred_at=now,
                        payload={
                            "line": line.line,
                            "timestamp": line.timestamp,
                            "stream": line.stream,
                            "parser": "docker-text-v1",
                            "container_id": snapshot.container_id,
                        },
                        evidence_ref=(
                            f"docker-log://{snapshot.container_id}/{line.timestamp}/"
                            f"{line.content_hash}"
                        ),
                    )
                    events.append(event)
                    if severity in {Severity.ERROR, Severity.CRITICAL}:
                        incident_candidates.append((fingerprint_event(event), event))
            except DockerTransportError:
                add(
                    "docker.logs.unavailable",
                    Severity.WARNING,
                    {"container_id": snapshot.container_id},
                )

        last_heartbeat = self._parse_time(prior.get("last_heartbeat_at"))
        heartbeat_due = (
            last_heartbeat is None
            or (now - last_heartbeat).total_seconds() >= source.heartbeat_seconds
        )
        if not events and heartbeat_due:
            add(
                "docker.heartbeat",
                Severity.INFO,
                {
                    "state": snapshot.state,
                    "health": snapshot.health,
                    "memory_bytes": snapshot.memory_bytes,
                },
            )
        heartbeat_at = (
            now.isoformat() if events else prior.get("last_heartbeat_at") or now.isoformat()
        )
        state = SourceState(
            project_id=project_id,
            source_id=source.source_id,
            source_kind="docker",
            sequence=sequence,
            observed_at=now,
            state={
                "presence": "available",
                "container_id": snapshot.container_id,
                "name": snapshot.name,
                "image": snapshot.image,
                "state": snapshot.state,
                "status": snapshot.status,
                "health": snapshot.health,
                "restart_count": snapshot.restart_count,
                "exit_code": snapshot.exit_code,
                "oom_killed": snapshot.oom_killed,
                "started_at": snapshot.started_at,
                "finished_at": snapshot.finished_at,
                "memory_bytes": snapshot.memory_bytes,
                "memory_high": memory_high,
                "last_heartbeat_at": heartbeat_at,
                "log_cursor": next_log_cursor.model_dump(mode="json"),
            },
        )
        return SourceObservation(
            events=tuple(events),
            state=state,
            incident_candidates=tuple(incident_candidates),
        )

    @staticmethod
    def _previous_log_cursor(
        prior: dict[str, object],
        container_id: str,
    ) -> DockerLogCursor:
        if prior.get("presence") != "available" or prior.get("container_id") != container_id:
            return DockerLogCursor()
        raw = prior.get("log_cursor")
        if not isinstance(raw, dict):
            return DockerLogCursor()
        return DockerLogCursor.model_validate(raw)

    @staticmethod
    def _high(
        threshold: int | None,
        value: int | None,
        previously_high: bool,
    ) -> bool:
        if threshold is None or value is None:
            return False
        limit = threshold * (0.9 if previously_high else 1.0)
        return value >= limit

    @staticmethod
    def _classify_severity(line: str) -> Severity:
        normalized = line.upper()
        error_markers = ("ERROR", "FAILED", "FATAL", "PANIC", "EXCEPTION")
        if any(marker in normalized for marker in error_markers):
            return Severity.ERROR
        if "WARN" in normalized:
            return Severity.WARNING
        return Severity.INFO

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        return datetime.fromisoformat(value) if isinstance(value, str) else None

    @staticmethod
    def _event(
        project_id: str,
        run_id: str,
        source_id: str,
        sequence: int,
        event_type: str,
        severity: Severity,
        payload: dict[str, object],
        evidence_prefix: str,
        occurred_at: datetime,
    ) -> EventRecord:
        digest = sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()
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
            evidence_ref=f"{evidence_prefix}#sha256={digest}",
        )
