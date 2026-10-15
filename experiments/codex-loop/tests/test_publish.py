"""Offline publisher integration tests; local bare Git remote and fake gh, no network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import_path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(import_path))
import publish  # noqa: E402

REPOSITORY = "test-owner/test-repo"


def command(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        command("git", "init", "-b", "main", str(self.repo))
        command("git", "-C", str(self.repo), "config", "user.name", "Local Pilot")
        command("git", "-C", str(self.repo), "config", "user.email", "pilot@example.invalid")
        (self.repo / "README.md").write_text("start\n", encoding="utf-8")
        command("git", "-C", str(self.repo), "add", "README.md")
        command("git", "-C", str(self.repo), "commit", "-m", "initial")
        command("git", "-C", str(self.repo), "remote", "add", "origin",
                "https://github.com/test-owner/test-repo.git")
        self.taskid = "123456789abc"
        self.work = self.root / "shared" / self.taskid
        self.work.parent.mkdir()
        command("git", "-C", str(self.repo), "worktree", "add", "-b",
                "auto/codex-" + self.taskid, str(self.work), "HEAD")
        (self.work / "hello.py").write_text('print("hello")\n', encoding="utf-8")
        self.data = self.root / "data"
        self.run_dir = self.data / "runs" / self.taskid
        self.run_dir.mkdir(parents=True)
        self.checker = import_path / "verify-smoke.py"
        digest, files = publish.artifact_snapshot(self.work)
        self.state = {
            "task_id": self.taskid, "status": "LOCAL_PASS",
            "validation": "PASS", "base_branch": "main",
            "base_sha": command("git", "-C", str(self.repo), "rev-parse", "HEAD").strip(),
            "branch": "auto/codex-" + self.taskid,
            "repo": str(self.repo), "worktree": str(self.work),
            "checks": [f"python3 {self.checker}"],
            "artifact_sha256": digest, "changed_files": files,
        }
        (self.run_dir / "state.json").write_text(json.dumps(self.state), encoding="utf-8")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['auth','status']: sys.exit(0)\n"
            "if args[:2] == ['repo','view']: print('WRITE'); sys.exit(0)\n"
            "f = pathlib.Path(os.environ['FAKE_GH_PRFILE'])\n"
            "if args[:2] == ['pr','list']:\n"
            "    print(json.dumps([{'url':f.read_text(), 'isDraft':True, "
            "'headRefName':'auto/codex-123456789abc', 'baseRefName':'main'}] "
            "if f.exists() else [])); sys.exit(0)\n"
            "if args[:2] == ['pr','create']:\n"
            "    f.write_text('https://github.com/test-owner/test-repo/pull/42')\n"
            "    print(f.read_text()); sys.exit(0)\n"
            "sys.exit(3)\n", encoding="utf-8",
        )
        gh.chmod(0o755)
        self.env = dict(os.environ)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["FAKE_GH_PRFILE"] = str(self.root / "pr-url")
        self.bare = self.root / "remote.git"
        command("git", "init", "--bare", str(self.bare))

    def execute(self, *, commit=False):
        args = SimpleNamespace(task_id=self.taskid, repository=REPOSITORY, execute=commit)
        original_git = publish.git

        def fake_push(repo, *argv, **kwargs):
            if argv and argv[0] == "push":
                return original_git(repo, "push", "--set-upstream", str(self.bare),
                                    "HEAD:refs/heads/auto/codex-" + self.taskid, timeout=60)
            return original_git(repo, *argv, **kwargs)

        with patch.object(publish, "RUNTIME", self.data), patch.object(publish, "git", fake_push):
            with patch.dict(os.environ, self.env):
                return publish.run(args)

    def state_file(self):
        return json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))

    def test_dry_run_does_not_commit_push_or_create_pr(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.state_file()["status"], "LOCAL_PASS")
        self.assertFalse((self.root / "pr-url").exists())
        self.assertEqual(command("git", "-C", str(self.work), "rev-parse", "HEAD").strip(),
                         self.state["base_sha"])

    def test_edit_after_validation_blocks_publish(self):
        (self.work / "hello.py").write_text('print("unexpected")\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "artifacts differ"):
            self.execute(commit=True)
        self.assertEqual(self.state_file()["status"], "LOCAL_PASS")

    def test_explicit_publish_is_idempotent(self):
        self.assertEqual(self.execute(commit=True), 0)
        self.assertEqual(self.state_file()["status"], "PR_DRAFT")
        self.assertEqual(self.execute(commit=True), 0)
        self.assertEqual(self.state_file()["pr_url"],
                         "https://github.com/test-owner/test-repo/pull/42")
        self.assertEqual(command("git", "--git-dir", str(self.bare),
                                 "rev-parse", "refs/heads/auto/codex-" + self.taskid).strip(),
                         self.state_file()["publish_commit"])


if __name__ == "__main__":
    unittest.main()
