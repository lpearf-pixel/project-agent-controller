from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from project_agent_controller.domain.models import EventRecord, ProcessSourceConfig, Severity
from project_agent_controller.observer.contracts import SourceObservation, SourceState
from project_agent_controller.observer.file_source import resolve_local_path
from project_agent_controller.observer.process_provider import (
    ProcessProvider,
    ProcessSnapshot,
    ProcessUnavailable,
    ProcessUnavailableKind,
)


class ProcessSourceObserver:
    def __init__(
        self,
        local_root: Path,
        provider: ProcessProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.local_root = local_root
        self.provider = provider
        self.clock = clock or (lambda: datetime.now(UTC))

    def observe(
        self,
        project_id: str,
        run_id: str,
        source: ProcessSourceConfig,
        previous: SourceState | None,
    ) -> SourceObservation:
        now = self.clock()
        try:
            pid = self._read_pid(source)
        except FileNotFoundError:
            return self._unavailable(project_id, run_id, source, previous, now, "missing", None)
        except ValueError:
            return self._unavailable(project_id, run_id, source, previous, now, "invalid_pid", None)

        try:
            snapshot = self.provider.snapshot(pid)
        except ProcessUnavailable as error:
            return self._unavailable(
                project_id,
                run_id,
                source,
                previous,
                now,
                error.kind.value,
                pid,
            )
        return self._available(project_id, run_id, source, previous, now, snapshot)

    def _read_pid(self, source: ProcessSourceConfig) -> int:
        path = resolve_local_path(source.pid_file_ref, self.local_root)
        raw = path.read_text(encoding="utf-8").strip()
        if not raw.isdecimal():
            raise ValueError("PID file must contain a positive decimal integer")
        pid = int(raw)
        if pid <= 0:
            raise ValueError("PID must be positive")
        return pid

    def _unavailable(
        self,
        project_id: str,
        run_id: str,
        source: ProcessSourceConfig,
        previous: SourceState | None,
        now: datetime,
        error_kind: str,
        pid: int | None,
    ) -> SourceObservation:
        prior = previous.state if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0
        event_type = {
            "missing": "process.missing",
            "invalid_pid": "process.pid.invalid",
            ProcessUnavailableKind.ACCESS_DENIED.value: "process.access.denied",
            ProcessUnavailableKind.ZOMBIE.value: "process.zombie",
        }.get(error_kind, "process.missing")
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
                    {"pid": pid, "error_kind": error_kind},
                    f"process://{pid or 'unknown'}/{error_kind}",
                    now,
                )
            )
        state = SourceState(
            project_id=project_id,
            source_id=source.source_id,
            source_kind="process",
            sequence=sequence,
            observed_at=now,
            state={
                "presence": "unavailable",
                "pid": pid,
                "error_kind": error_kind,
                "last_heartbeat_at": prior.get("last_heartbeat_at") or now.isoformat(),
            },
        )
        return SourceObservation(events=tuple(events), state=state)

    def _available(
        self,
        project_id: str,
        run_id: str,
        source: ProcessSourceConfig,
        previous: SourceState | None,
        now: datetime,
        snapshot: ProcessSnapshot,
    ) -> SourceObservation:
        prior = previous.state if previous is not None else {}
        sequence = previous.sequence if previous is not None else 0
        events: list[EventRecord] = []

        def add(event_type: str, severity: Severity, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            events.append(
                self._event(
                    project_id,
                    run_id,
                    source.source_id,
                    sequence,
                    event_type,
                    severity,
                    payload,
                    f"process://{snapshot.pid}/{snapshot.create_time}",
                    now,
                )
            )

        previously_available = prior.get("presence") == "available"
        identity_changed = previously_available and (
            int(prior.get("pid", -1)) != snapshot.pid
            or float(prior.get("create_time", -1.0)) != snapshot.create_time
        )
        if not previously_available:
            add(
                "process.available",
                Severity.INFO,
                {"pid": snapshot.pid, "name": snapshot.name, "status": snapshot.status},
            )
        elif identity_changed:
            add(
                "process.restarted",
                Severity.WARNING,
                {
                    "previous_pid": prior.get("pid"),
                    "pid": snapshot.pid,
                    "previous_create_time": prior.get("create_time"),
                    "create_time": snapshot.create_time,
                },
            )
        elif prior.get("status") != snapshot.status:
            add(
                "process.state.changed",
                Severity.INFO,
                {"previous": prior.get("status"), "current": snapshot.status},
            )

        cpu_percent = self._cpu_percent(previous, snapshot, now)
        previous_cpu_high = bool(prior.get("cpu_high", False))
        previous_rss_high = bool(prior.get("rss_high", False))
        cpu_high = self._high(source.cpu_warning_percent, cpu_percent, previous_cpu_high)
        rss_high = self._high(source.rss_warning_bytes, snapshot.rss_bytes, previous_rss_high)
        crossed: list[str] = []
        recovered: list[str] = []
        if cpu_high and not previous_cpu_high:
            crossed.append("cpu")
        if rss_high and not previous_rss_high:
            crossed.append("rss")
        if previous_cpu_high and not cpu_high:
            recovered.append("cpu")
        if previous_rss_high and not rss_high:
            recovered.append("rss")
        metrics: dict[str, object] = {
            "cpu_percent": cpu_percent,
            "rss_bytes": snapshot.rss_bytes,
        }
        if crossed:
            add(
                "process.resource.threshold",
                Severity.WARNING,
                {**metrics, "crossed": crossed},
            )
        if recovered:
            add(
                "process.resource.recovered",
                Severity.INFO,
                {**metrics, "recovered": recovered},
            )

        last_heartbeat = self._parse_time(prior.get("last_heartbeat_at"))
        heartbeat_due = (
            last_heartbeat is None
            or (now - last_heartbeat).total_seconds() >= source.heartbeat_seconds
        )
        if not events and heartbeat_due:
            add("process.heartbeat", Severity.INFO, metrics)
        heartbeat_at = (
            now.isoformat() if events else prior.get("last_heartbeat_at") or now.isoformat()
        )
        state = SourceState(
            project_id=project_id,
            source_id=source.source_id,
            source_kind="process",
            sequence=sequence,
            observed_at=now,
            state={
                "presence": "available",
                "pid": snapshot.pid,
                "create_time": snapshot.create_time,
                "status": snapshot.status,
                "name": snapshot.name,
                "executable": snapshot.executable,
                "cpu_seconds": snapshot.cpu_seconds,
                "cpu_percent": cpu_percent,
                "rss_bytes": snapshot.rss_bytes,
                "child_count": snapshot.child_count if source.include_children else 0,
                "cpu_high": cpu_high,
                "rss_high": rss_high,
                "last_heartbeat_at": heartbeat_at,
            },
        )
        return SourceObservation(events=tuple(events), state=state)

    @staticmethod
    def _cpu_percent(
        previous: SourceState | None, snapshot: ProcessSnapshot, now: datetime
    ) -> float:
        if previous is None or previous.state.get("presence") != "available":
            return 0.0
        prior_cpu = float(previous.state.get("cpu_seconds", snapshot.cpu_seconds))
        elapsed = (now - previous.observed_at).total_seconds()
        if elapsed <= 0:
            return 0.0
        return max(0.0, (snapshot.cpu_seconds - prior_cpu) / elapsed * 100.0)

    @staticmethod
    def _high(threshold: float | int | None, value: float | int, previously_high: bool) -> bool:
        if threshold is None:
            return False
        limit = float(threshold) * (0.9 if previously_high else 1.0)
        return float(value) >= limit

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _event(
        project_id: str,
        run_id: str,
        source_id: str,
        sequence: int,
        event_type: str,
        severity: Severity,
        payload: dict[str, object],
        evidence_ref: str,
        occurred_at: datetime,
    ) -> EventRecord:
        material = repr(sorted(payload.items())).encode("utf-8")
        digest = sha256(material).hexdigest()
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
            evidence_ref=f"{evidence_ref}#sha256={digest}",
        )
