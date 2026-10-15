"""Regression tests for false sandbox alarms and opt-in recovery without Codex."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recover_blocked
from loop import codex_result


def git(*args):
    import subprocess

    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class SandboxClassifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "codex-0.jsonl"

    def events(self, *events):
        self.log.write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
        )
        return codex_result(self.log)

    def test_normal_file_read_containing_old_sandbox_error_is_not_a_failure(self):
        accepted, reason = self.events(
            {"type": "item.completed", "item": {
                "type": "command_execution",
                "exit_code": 0,
                "aggregated_output": (
                    "Historical README: sandbox failed with a network-namespace error."
                ),
            }},
            {"type": "item.completed", "item": {
                "type": "agent_message",
                "text": "I read code mentioning bwrap: and rtm_newaddr.",
            }},
            {"type": "turn.completed"},
        )
        self.assertTrue(accepted, reason)

    def test_typed_namespace_error_still_blocks(self):
        accepted, reason = self.events(
            {"type": "error", "message": "bwrap: failed to create network namespace"},
            {"type": "turn.completed"},
        )
        self.assertFalse(accepted)
        self.assertIn("sandbox", reason.lower())

    def test_file_change_failed_still_blocks(self):
        accepted, reason = self.events(
            {"type": "item.completed", "item": {
                "type": "file_change", "status": "failed", "changes": [],
            }},
            {"type": "turn.completed"},
        )
        self.assertFalse(accepted)
        self.assertIn("file_change", reason)


class BlockedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.work = self.root / "worktrees" / "123456789abc"
        self.data = self.root / "native-runtime"
        self.run_dir = self.data / "runs" / "123456789abc"
        self.run_dir.mkdir(parents=True)
        self.repo.mkdir()
        git("init", "-b", "main", str(self.repo))
        git("-C", str(self.repo), "config", "user.name", "Test")
        git("-C", str(self.repo), "config", "user.email", "test@example.invalid")
        (self.repo / "README.md").write_text("initial\n", encoding="utf-8")
        git("-C", str(self.repo), "add", "README.md")
        git("-C", str(self.repo), "commit", "-m", "initial")
        self.base = git("-C", str(self.repo), "rev-parse", "HEAD")
        self.work.parent.mkdir(parents=True)
        git("-C", str(self.repo), "worktree", "add", "-b",
            "auto/codex-123456789abc", str(self.work), self.base)
        (self.work / "result.txt").write_text("done\n", encoding="utf-8")
        self.log = self.run_dir / "codex-0.jsonl"
        self.log.write_text(
            json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "exit_code": 0,
                "aggregated_output": "source line: sandbox failed",
            }}) + "\n" + json.dumps({"type": "turn.completed"}) + "\n",
            encoding="utf-8",
        )
        self.state = {
            "task_id": "123456789abc",
            "status": "BLOCKED",
            "reason": "Linux sandbox/namespace initialization failed; old classifier",
            "validation": "PENDING",
            "base_sha": self.base,
            "base_branch": "main",
            "branch": "auto/codex-123456789abc",
            "repo": str(self.repo),
            "worktree": str(self.work),
            "checks": [
                "python3 -c 'import pathlib; assert pathlib.Path(\"result.txt\").exists()'"
            ],
            "repair_history": [],
        }
        self.state_path = self.run_dir / "state.json"
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def recover(self, execute=False):
        with (
            patch.object(recover_blocked, "RUNTIME", self.data),
            patch.dict("os.environ", {"PAC_CODEX_SHARED_ROOT": str(self.root)}),
        ):
            return recover_blocked.recheck("123456789abc", execute=execute)

    def saved(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_dry_run_keeps_blocked(self):
        self.assertEqual(self.recover(), 0)
        self.assertEqual(self.saved()["status"], "BLOCKED")

    def test_revalidate_existing_worktree_without_codex_or_push(self):
        self.assertEqual(self.recover(execute=True), 0)
        state = self.saved()
        self.assertEqual(state["status"], "LOCAL_PASS")
        self.assertEqual(state["validation"], "PASS")
        self.assertEqual(state["changed_files"], ["result.txt"])
        self.assertTrue(state["artifact_sha256"])
        self.assertEqual(git("-C", str(self.work), "rev-parse", "HEAD"), self.base)
        self.assertTrue((self.work / "result.txt").exists())

    def test_actual_failed_file_change_is_not_reclassified(self):
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "item.completed", "item": {
                "type": "file_change", "status": "failed",
            }}) + "\n")
        with self.assertRaisesRegex(RuntimeError, "still signals a failure"):
            self.recover(execute=True)
        self.assertEqual(self.saved()["status"], "BLOCKED")

    def test_bad_validator_cannot_promote_blocked_task(self):
        self.state["checks"] = ["python3 -c 'raise SystemExit(1)'"]
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "validation failed"):
            self.recover(execute=True)
        self.assertEqual(self.saved()["status"], "BLOCKED")

    def test_protected_paths_block_recovery(self):
        (self.work / "AGENTS.md").write_text("do not publish\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "protected files changed"):
            self.recover(execute=True)


if __name__ == "__main__":
    unittest.main()
