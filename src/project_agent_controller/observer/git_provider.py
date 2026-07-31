from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from project_agent_controller.domain.models import GitSourceConfig
from project_agent_controller.observer.git_transport import GitReadTransport


class GitSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    head_sha: str | None = None
    branch: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int = Field(default=0, ge=0)
    behind: int = Field(default=0, ge=0)
    staged_count: int = Field(default=0, ge=0)
    unstaged_count: int = Field(default=0, ge=0)
    untracked_count: int = Field(default=0, ge=0)
    conflict_count: int = Field(default=0, ge=0)
    dirty: bool = False
    change_fingerprint: str
    remote_tracking_only: bool = True
    remote_freshness: str = "unknown"


def parse_porcelain_v2(text: str) -> GitSnapshot:
    head_sha: str | None = None
    branch: str | None = None
    detached = False
    upstream: str | None = None
    ahead = 0
    behind = 0
    staged = 0
    unstaged = 0
    untracked = 0
    conflicts = 0
    record_material: list[str] = []

    for line in text.splitlines():
        if line.startswith("# branch.oid "):
            value = line.removeprefix("# branch.oid ").strip()
            head_sha = None if value == "(initial)" else value
        elif line.startswith("# branch.head "):
            value = line.removeprefix("# branch.head ").strip()
            if value in {"(detached)", "(unknown)"}:
                detached = True
                branch = None
            else:
                branch = value
        elif line.startswith("# branch.upstream "):
            upstream = line.removeprefix("# branch.upstream ").strip()
        elif line.startswith("# branch.ab "):
            parts = line.removeprefix("# branch.ab ").split()
            for part in parts:
                if part.startswith("+"):
                    ahead = int(part[1:])
                elif part.startswith("-"):
                    behind = int(part[1:])
        elif line.startswith(("1 ", "2 ")):
            tokens = line.split(" ", 2)
            xy = tokens[1] if len(tokens) > 1 else ".."
            if len(xy) >= 2:
                staged += int(xy[0] != ".")
                unstaged += int(xy[1] != ".")
            record_material.append(line[: min(len(line), 80)])
        elif line.startswith("u "):
            conflicts += 1
            record_material.append(line[: min(len(line), 80)])
        elif line.startswith("? "):
            untracked += 1
            record_material.append("?")

    dirty = any((staged, unstaged, untracked, conflicts))
    material = "\n".join(
        [
            head_sha or "",
            branch or "",
            upstream or "",
            str(ahead),
            str(behind),
            str(staged),
            str(unstaged),
            str(untracked),
            str(conflicts),
            *record_material,
        ]
    ).encode("utf-8")
    return GitSnapshot(
        head_sha=head_sha,
        branch=branch,
        detached=detached,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        staged_count=staged,
        unstaged_count=unstaged,
        untracked_count=untracked,
        conflict_count=conflicts,
        dirty=dirty,
        change_fingerprint=f"sha256:{sha256(material).hexdigest()}",
    )


def resolve_git_path(path_ref: str, local_root: Path) -> Path:
    if not path_ref.startswith("local://"):
        raise ValueError("path_ref must use local://")
    relative = path_ref.removeprefix("local://")
    if not relative:
        raise ValueError("path_ref must not be empty")
    root = local_root.resolve()
    candidate = (root / relative).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("resolved git path escapes local root")
    return candidate


class GitRepositoryProvider:
    def __init__(self, local_root: Path, transport: GitReadTransport) -> None:
        self.local_root = local_root
        self.transport = transport

    def snapshot(self, source: GitSourceConfig) -> GitSnapshot:
        path = resolve_git_path(source.path_ref, self.local_root)
        return parse_porcelain_v2(
            self.transport.status(path, include_untracked=source.include_untracked)
        )


class UnavailableGitProvider:
    def __init__(self, reason: str = "git executable is unavailable") -> None:
        self.reason = reason

    def snapshot(self, _source: GitSourceConfig) -> GitSnapshot:
        from project_agent_controller.observer.git_transport import GitTransportError

        raise GitTransportError(self.reason)
