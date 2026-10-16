"""Atomic state and evidence store for one PAC runner."""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

STATES = {"QUEUED", "CLAIMED", "CODING", "VALIDATING", "LOCAL_PASS", "PUBLISHING",
          "PR_DRAFT", "CI_CHECKING", "CI_PASS", "BLOCKED", "FAILED", "CI_FAIL",
          "TIMEOUT", "NEEDS_HARDWARE"}
TERMINAL = {"CI_PASS", "BLOCKED", "FAILED", "CI_FAIL", "TIMEOUT", "NEEDS_HARDWARE"}
ALLOWED = {"QUEUED": {"CLAIMED", "BLOCKED"}, "CLAIMED": {"CODING", "BLOCKED", "FAILED"},
           "CODING": {"VALIDATING", "BLOCKED", "FAILED", "TIMEOUT"},
           "VALIDATING": {"LOCAL_PASS", "CODING", "FAILED", "BLOCKED"},
           "LOCAL_PASS": {"PUBLISHING", "BLOCKED", "FAILED"},
           "PUBLISHING": {"PR_DRAFT", "FAILED", "BLOCKED"},
           "PR_DRAFT": {"CI_CHECKING", "FAILED"}, "CI_CHECKING": {"CI_PASS", "CI_FAIL", "TIMEOUT", "FAILED"}}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class StateStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.runs = self.root / "runs"
        self.lock_path = self.root / "runner.lock"

    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = self.lock_path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError("another PAC runner is active")
        return handle

    def path(self, task_id: str) -> Path:
        return self.runs / task_id / "state.json"

    def read(self, task_id: str) -> dict:
        return json.loads(self.path(task_id).read_text(encoding="utf-8"))

    def create(self, task_id: str, values: dict) -> dict:
        path = self.path(task_id)
        if path.exists():
            return self.read(task_id)
        now = time.time()
        state = {**values, "task_id": task_id, "status": "QUEUED", "created_at": now,
                 "updated_at": now, "transition_count": 0}
        _write(path, state)
        self._evidence(task_id, None, "QUEUED", "task discovered", state)
        return state

    def transition(self, task_id: str, next_status: str, reason: str, **updates) -> dict:
        if next_status not in STATES:
            raise ValueError("unknown state")
        state = self.read(task_id)
        current = state["status"]
        if current == next_status:
            return state
        if current in TERMINAL:
            raise ValueError("terminal task cannot transition")
        if next_status not in ALLOWED.get(current, set()):
            raise ValueError(f"invalid transition {current} -> {next_status}")
        state.update(updates)
        state.update(status=next_status, updated_at=time.time(),
                     transition_count=state.get("transition_count", 0) + 1, reason=reason)
        _write(self.path(task_id), state)
        self._evidence(task_id, current, next_status, reason, state)
        return state

    def _evidence(self, task_id, previous, current, reason, state):
        record = {"at": time.time(), "task_id": task_id, "from": previous, "to": current,
                  "reason": str(reason)[:400], "base_sha": state.get("base_sha"),
                  "publish_commit": state.get("publish_commit")}
        path = self.runs / task_id / "transitions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
