"""Offline tests for recovering uncommitted Runner edits from a read-only source."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import migrate_runner


def git(*args):
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class RunnerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "readonly-src"
        self.dest = self.root / "ext4-dest"
        self.source.mkdir()
        git("init", "-b", "feat/codex-loop-v0-20261015", str(self.source))
        for repo in (self.source,):
            git("-C", str(repo), "config", "user.name", "Offline Test")
            git("-C", str(repo), "config", "user.email", "test@example.invalid")
        baseline = self.source / "experiments" / "codex-loop" / "README.md"
        baseline.parent.mkdir(parents=True)
        baseline.write_text("original\n", encoding="utf-8")
        git("-C", str(self.source), "add", ".")
        git("-C", str(self.source), "commit", "-m", "initial")
        git("clone", "-q", str(self.source), str(self.dest))
        git("-C", str(self.dest), "config", "user.name", "Offline Test")
        git("-C", str(self.dest), "config", "user.email", "test@example.invalid")
        git("-C", str(self.dest), "checkout", "-b", migrate_runner.TARGET_BRANCH)
        self.new = self.source / "experiments" / "codex-loop" / "runner" / "agent.py"
        self.new.parent.mkdir(parents=True)
        self.new.write_text("print('hello')\n", encoding="utf-8")

    def test_dry_run_keeps_destination_clean(self):
        plan = migrate_runner.prepare(self.source, self.dest)
        self.assertEqual(plan["accepted"], ["experiments/codex-loop/runner/agent.py"])
        self.assertEqual(migrate_runner.migrate(self.source, self.dest), 0)
        self.assertFalse((self.dest / "experiments/codex-loop/runner/agent.py").exists())

    def test_apply_copies_without_committing_or_modifying_source(self):
        original = git("-C", str(self.dest), "rev-parse", "HEAD")
        self.assertEqual(migrate_runner.migrate(self.source, self.dest, apply=True), 0)
        self.assertEqual(git("-C", str(self.dest), "rev-parse", "HEAD"), original)
        self.assertEqual(
            (self.dest / "experiments/codex-loop/runner/agent.py").read_text(),
            self.new.read_text(),
        )

    def test_remote_existing_file_conflict_refuses_everything(self):
        remote = self.dest / "experiments/codex-loop/README.md"
        remote.write_text("remote update\n", encoding="utf-8")
        git("-C", str(self.dest), "add", ".")
        git("-C", str(self.dest), "commit", "-m", "later remote edit")
        (self.source / "experiments/codex-loop/README.md").write_text("local edit\n")
        plan = migrate_runner.prepare(self.source, self.dest)
        self.assertIn("experiments/codex-loop/README.md", plan["conflicts"])
        with self.assertRaisesRegex(RuntimeError, "nothing copied"):
            migrate_runner.migrate(self.source, self.dest, apply=True)
        self.assertFalse((self.dest / "experiments/codex-loop/runner/agent.py").exists())

    def test_protected_path_fails_closed(self):
        (self.source / "experiments/codex-loop/.env").write_text("SECRET=x\n")
        plan = migrate_runner.prepare(self.source, self.dest)
        self.assertIn("experiments/codex-loop/.env", plan["rejected"])
        with self.assertRaisesRegex(RuntimeError, "nothing copied"):
            migrate_runner.migrate(self.source, self.dest, apply=True)

    def test_destination_must_remain_clean(self):
        (self.dest / "tmp.log").write_text("nope\n")
        with self.assertRaisesRegex(RuntimeError, "already contains local changes"):
            migrate_runner.prepare(self.source, self.dest)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            migrate_runner.prepare(self.root / "unknown", self.dest)


if __name__ == "__main__":
    unittest.main()
