"""Credential isolation checks; absence is a hard failure."""
from __future__ import annotations

import os
import pwd
from pathlib import Path


def _uid(name: str) -> int | None:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def check(*, codex_user: str, publisher_user: str, runner_user: str | None = None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    ids = {name: _uid(name) for name in (codex_user, publisher_user, runner_user) if name}
    if ids.get(codex_user) is None:
        errors.append(f"missing OS user: {codex_user}")
    if ids.get(publisher_user) is None:
        errors.append(f"missing OS user: {publisher_user}")
    if ids.get(codex_user) == ids.get(publisher_user):
        errors.append("Codex and Publisher share an OS identity")
    if runner_user and ids.get(runner_user) in (ids.get(codex_user), ids.get(publisher_user)):
        errors.append("runner identity must be distinct from Codex and Publisher")
    home = Path(os.environ.get("HOME", "")).resolve()
    if str(home) in ("/", ""):
        errors.append("invalid runner HOME")
    sensitive_names = ("TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY")
    inherited = {key for key in os.environ if any(word in key.upper() for word in sensitive_names)
                 or key.upper() in {"GH_TOKEN", "GITHUB_TOKEN"}}
    if inherited:
        errors.append("credential-like environment inherited: " + ",".join(sorted(inherited)))
    return not errors, errors


def codex_environment(codex_home: Path) -> dict[str, str]:
    allowed = {"PATH", "LANG", "LC_ALL", "TZ"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["HOME"] = str(codex_home)
    return env
