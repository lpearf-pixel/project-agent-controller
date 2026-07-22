from enum import StrEnum
from uuid import uuid4

from project_agent_controller.storage.database import Database


class ControllerState(StrEnum):
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    DRAINED = "DRAINED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"


LEGAL_TRANSITIONS: dict[ControllerState, set[ControllerState]] = {
    ControllerState.ACTIVE: {
        ControllerState.DRAINING,
        ControllerState.EMERGENCY_STOP,
        ControllerState.DEGRADED,
    },
    ControllerState.DRAINING: {
        ControllerState.DRAINED,
        ControllerState.EMERGENCY_STOP,
        ControllerState.DEGRADED,
    },
    ControllerState.DRAINED: {
        ControllerState.RECOVERING,
        ControllerState.EMERGENCY_STOP,
    },
    ControllerState.EMERGENCY_STOP: {ControllerState.RECOVERING},
    ControllerState.RECOVERING: {
        ControllerState.ACTIVE,
        ControllerState.DRAINING,
        ControllerState.DRAINED,
        ControllerState.EMERGENCY_STOP,
        ControllerState.DEGRADED,
    },
    ControllerState.DEGRADED: {
        ControllerState.RECOVERING,
        ControllerState.DRAINING,
        ControllerState.EMERGENCY_STOP,
    },
}


class ControlService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_state(self) -> ControllerState:
        return ControllerState(self.database.get_controller_state())

    def drain(self, *, actor: str, reason: str) -> ControllerState:
        self._validate_actor_reason(actor, reason)
        state = self.get_state()
        if state is ControllerState.DRAINED:
            return state
        if state is ControllerState.EMERGENCY_STOP:
            raise ValueError("cannot drain while controller is in EMERGENCY_STOP")
        if state is not ControllerState.DRAINING:
            self._transition(state, ControllerState.DRAINING, actor=actor, reason=reason)
        self._transition(
            ControllerState.DRAINING,
            ControllerState.DRAINED,
            actor=actor,
            reason=reason,
        )
        return ControllerState.DRAINED

    def emergency_stop(self, *, actor: str, reason: str) -> ControllerState:
        self._validate_actor_reason(actor, reason)
        state = self.get_state()
        if state is ControllerState.EMERGENCY_STOP:
            return state
        self._transition(state, ControllerState.EMERGENCY_STOP, actor=actor, reason=reason)
        return ControllerState.EMERGENCY_STOP

    def clear_emergency_stop(self, *, actor: str, reason: str) -> ControllerState:
        self._validate_actor_reason(actor, reason)
        state = self.get_state()
        if state is not ControllerState.EMERGENCY_STOP:
            raise ValueError("controller is not in EMERGENCY_STOP")
        self._transition(
            ControllerState.EMERGENCY_STOP,
            ControllerState.RECOVERING,
            actor=actor,
            reason=reason,
        )
        return ControllerState.RECOVERING

    def _transition(
        self,
        current: ControllerState,
        next_state: ControllerState,
        *,
        actor: str,
        reason: str,
    ) -> None:
        if next_state not in LEGAL_TRANSITIONS[current]:
            raise ValueError(f"illegal controller transition: {current} -> {next_state}")
        self.database.transition_controller_state(
            allowed_from={current.value},
            next_state=next_state.value,
            actor=actor.strip(),
            reason=reason.strip(),
            request_id=str(uuid4()),
        )

    @staticmethod
    def _validate_actor_reason(actor: str, reason: str) -> None:
        if not actor.strip():
            raise ValueError("actor must not be empty")
        if not reason.strip():
            raise ValueError("reason must not be empty")
