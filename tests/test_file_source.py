from pathlib import Path

import pytest

from project_agent_controller.control.service import ControlService
from project_agent_controller.domain.models import FileSourceConfig, ProjectConfig
from project_agent_controller.observer.file_source import FileSourceReader, resolve_local_path
from project_agent_controller.observer.runner import ObservationBlocked, ObserverRunner
from project_agent_controller.storage.database import Database


def source() -> FileSourceConfig:
    return FileSourceConfig(source_id="app-log", path_ref="local://demo/app.log")


def project() -> ProjectConfig:
    return ProjectConfig(project_id="demo", display_name="Demo", sources=(source(),))


def test_resolve_local_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()

    with pytest.raises(ValueError, match="escapes local root"):
        resolve_local_path("local://../secret.log", root)


def test_reader_reads_only_new_complete_lines(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    path = root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("first\n", encoding="utf-8")
    reader = FileSourceReader(root)

    first = reader.read_available("demo", "run-1", source(), cursor=None)
    path.write_text("first\nsecond\n", encoding="utf-8")
    second = reader.read_available("demo", "run-1", source(), cursor=first.cursor)

    assert [event.payload["line"] for event in first.events] == ["first"]
    assert [event.payload["line"] for event in second.events] == ["second"]
    assert second.cursor.byte_offset == len("first\nsecond\n".encode())


def test_reader_holds_partial_line_until_newline(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    path = root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("partial", encoding="utf-8")
    reader = FileSourceReader(root)

    first = reader.read_available("demo", "run-1", source(), cursor=None)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(" line\n")
    second = reader.read_available("demo", "run-1", source(), cursor=first.cursor)

    assert first.events == ()
    assert first.cursor.byte_offset == 0
    assert [event.payload["line"] for event in second.events] == ["partial line"]


def test_reader_emits_truncation_and_rotation_notices(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    path = root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("one\ntwo\n", encoding="utf-8")
    reader = FileSourceReader(root)
    first = reader.read_available("demo", "run-1", source(), cursor=None)

    path.write_text("new\n", encoding="utf-8")
    truncated = reader.read_available("demo", "run-1", source(), cursor=first.cursor)
    assert [event.event_type for event in truncated.events] == ["source.truncated", "log.line"]

    rotated_path = path.with_suffix(".old")
    path.rename(rotated_path)
    path.write_text("rotated\n", encoding="utf-8")
    rotated = reader.read_available("demo", "run-1", source(), cursor=truncated.cursor)
    assert [event.event_type for event in rotated.events] == ["source.rotated", "log.line"]


def test_reader_emits_missing_warning_without_crashing(tmp_path: Path) -> None:
    reader = FileSourceReader(tmp_path / "logs")

    batch = reader.read_available("demo", "run-1", source(), cursor=None)

    assert [event.event_type for event in batch.events] == ["source.missing"]
    assert batch.cursor.byte_offset == 0


def test_runner_persists_events_and_stop_blocks_observation(settings, tmp_path: Path) -> None:
    local_root = tmp_path / "logs"
    path = local_root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("ready\n", encoding="utf-8")
    database = Database(settings.database_path)
    database.initialize()
    control = ControlService(database)
    runner = ObserverRunner(database, control, local_root=local_root, run_id="run-1")

    assert runner.observe_once(project()) == 1
    assert [event.payload["line"] for event in database.list_events("demo")] == ["ready"]

    control.emergency_stop(actor="local-admin", reason="test stop")
    with pytest.raises(ObservationBlocked, match="EMERGENCY_STOP"):
        runner.observe_once(project())


def test_reader_classifies_error_lines_for_incident_curation(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    path = root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("ERROR E900 database timeout\n", encoding="utf-8")
    reader = FileSourceReader(root)

    batch = reader.read_available("demo", "run-1", source(), cursor=None)

    assert batch.events[0].severity.value == "error"


def test_missing_source_is_coalesced_until_file_becomes_available(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    reader = FileSourceReader(root)

    first = reader.read_available("demo", "run-1", source(), cursor=None)
    second = reader.read_available("demo", "run-1", source(), cursor=first.cursor)
    path = root / "demo/app.log"
    path.parent.mkdir(parents=True)
    path.write_text("recovered\n", encoding="utf-8")
    recovered = reader.read_available("demo", "run-1", source(), cursor=second.cursor)

    assert [event.event_type for event in first.events] == ["source.missing"]
    assert second.events == ()
    assert [event.event_type for event in recovered.events] == [
        "source.available",
        "log.line",
    ]
