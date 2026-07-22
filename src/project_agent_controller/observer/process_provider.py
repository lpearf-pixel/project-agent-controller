from enum import StrEnum
from pathlib import Path
from typing import Protocol

import psutil
from pydantic import BaseModel, ConfigDict, Field


class ProcessUnavailableKind(StrEnum):
    MISSING = "missing"
    ACCESS_DENIED = "access_denied"
    ZOMBIE = "zombie"


class ProcessUnavailable(RuntimeError):
    def __init__(self, kind: ProcessUnavailableKind, pid: int) -> None:
        self.kind = kind
        self.pid = pid
        super().__init__(f"process {pid} unavailable: {kind.value}")


class ProcessSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int = Field(gt=0)
    create_time: float
    status: str
    name: str
    executable: str | None
    cpu_seconds: float = Field(ge=0)
    rss_bytes: int = Field(ge=0)
    child_count: int = Field(ge=0)


class ProcessProvider(Protocol):
    def snapshot(self, pid: int) -> ProcessSnapshot: ...


class PsutilProcessProvider:
    def snapshot(self, pid: int) -> ProcessSnapshot:
        try:
            process = psutil.Process(pid)
            with process.oneshot():
                times = process.cpu_times()
                try:
                    executable = Path(process.exe()).name
                except (psutil.AccessDenied, psutil.ZombieProcess):
                    executable = None
                return ProcessSnapshot(
                    pid=pid,
                    create_time=process.create_time(),
                    status=process.status(),
                    name=process.name(),
                    executable=executable,
                    cpu_seconds=float(times.user + times.system),
                    rss_bytes=int(process.memory_info().rss),
                    child_count=len(process.children(recursive=True)),
                )
        except psutil.NoSuchProcess as error:
            raise ProcessUnavailable(ProcessUnavailableKind.MISSING, pid) from error
        except psutil.AccessDenied as error:
            raise ProcessUnavailable(ProcessUnavailableKind.ACCESS_DENIED, pid) from error
        except psutil.ZombieProcess as error:
            raise ProcessUnavailable(ProcessUnavailableKind.ZOMBIE, pid) from error
