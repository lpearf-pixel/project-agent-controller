import pytest

from project_agent_controller.control.service import ControllerState, ControlService
from project_agent_controller.storage.database import Database


def test_controller_starts_active_and_drain_is_idempotent(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = ControlService(database)

    assert service.get_state() is ControllerState.ACTIVE
    assert service.drain(actor="local-user", reason="maintenance") is ControllerState.DRAINED
    assert service.drain(actor="local-user", reason="maintenance") is ControllerState.DRAINED

    transitions = database.list_control_events()
    assert [(item["previous_state"], item["next_state"]) for item in transitions] == [
        ("ACTIVE", "DRAINING"),
        ("DRAINING", "DRAINED"),
    ]


def test_emergency_stop_requires_explicit_recovery(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = ControlService(database)

    assert service.emergency_stop(actor="local-admin", reason="credential leak") is (
        ControllerState.EMERGENCY_STOP
    )
    assert service.emergency_stop(actor="local-admin", reason="duplicate request") is (
        ControllerState.EMERGENCY_STOP
    )
    assert service.clear_emergency_stop(
        actor="local-admin", reason="credential rotated"
    ) is ControllerState.RECOVERING
    assert service.get_state() is ControllerState.RECOVERING


def test_clear_emergency_stop_rejects_missing_reason_and_wrong_state(settings) -> None:
    database = Database(settings.database_path)
    database.initialize()
    service = ControlService(database)

    with pytest.raises(ValueError, match="controller is not in EMERGENCY_STOP"):
        service.clear_emergency_stop(actor="local-admin", reason="not stopped")

    service.emergency_stop(actor="local-admin", reason="test")
    with pytest.raises(ValueError, match="reason must not be empty"):
        service.clear_emergency_stop(actor="local-admin", reason="   ")
