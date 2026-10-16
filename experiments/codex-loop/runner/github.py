"""Small bounded wrapper around GitHub CLI; output is never shell input."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass
class GitHubClient:
    repository: str
    max_calls: int = 36
    calls: int = 0
    timeout: int = 40

    def call(self, args: list[str]) -> str:
        if self.calls >= self.max_calls:
            raise RuntimeError("GitHub API budget exhausted")
        self.calls += 1
        result = subprocess.run(["gh", *args], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=self.timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"GitHub command failed (call {self.calls})")
        return result.stdout

    def tasks(self) -> list[dict]:
        raw = self.call(["issue", "list", "--repo", self.repository, "--state", "open",
                         "--limit", "100", "--json", "number,title,body,author"])
        value = json.loads(raw)
        if not isinstance(value, list):
            raise RuntimeError("unexpected issue response")
        return [item for item in value if isinstance(item, dict)]

    def pr_view(self, url: str) -> dict:
        value = json.loads(self.call(["pr", "view", url, "--repo", self.repository,
                                      "--json", "isDraft,headRefName,headRefOid,baseRefName"]))
        if not isinstance(value, dict):
            raise RuntimeError("unexpected PR response")
        return value
