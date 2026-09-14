import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dual_agent


class RunnerTests(unittest.TestCase):
    def test_rejects_context_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            root.mkdir()
            (Path(d) / "outside").write_text("secret")
            with self.assertRaises(ValueError):
                dual_agent.read_context(root, ["../outside"])

    def test_rejects_nonlocal_model_endpoint(self):
        with patch.object(dual_agent, "URL", "http://0.0.0.0:11434"):
            with self.assertRaises(ValueError):
                dual_agent.http_json("/api/tags")

    def test_git_worktree_and_failure_report(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            for argv in (["git", "init"], ["git", "config", "user.name", "Test"],
                         ["git", "config", "user.email", "test@example.invalid"]):
                self.assertEqual(dual_agent.command(argv, repo)["exit_code"], 0)
            (repo / "a.txt").write_text("fixture")
            for argv in (["git", "add", "a.txt"], ["git", "commit", "-m", "fixture"]):
                self.assertEqual(dual_agent.command(argv, repo)["exit_code"], 0)
            with patch.object(dual_agent, "DATA", Path(d) / "data"), patch.object(
                dual_agent, "generate",
                return_value={"text": "fixture", "input_tokens": 10, "output_tokens": 3}
            ):
                task = {"id": "test-1", "repo": str(repo), "objective": "Inspect fixture",
                        "context": ["a.txt"], "tests": [["git", "rev-parse", "--is-inside-work-tree"]]}
                report = dual_agent.run_task(task)
                self.assertEqual(report["status"], "completed")
                self.assertEqual(report["local_tokens"], {"input": 10, "output": 3})
                self.assertTrue(Path(report["worktree"]).exists())
                self.assertTrue((Path(d) / "data/reports/test-1.json").exists())
                task["id"] = "test-2"
                task["tests"] = [["git", "not-a-command"]]
                failed = dual_agent.run_task(task)
                self.assertEqual(failed["status"], "tests_failed")
                self.assertEqual(failed["steps"]["tests"][0]["exit_code"], 1)
                self.assertEqual(json.loads((Path(d) / "data/reports/test-2.json").read_text())["status"],
                                 "tests_failed")

    def test_rejects_invalid_task(self):
        with self.assertRaises(ValueError):
            dual_agent.validate_task({"id": "../oops", "repo": ".", "objective": "Inspect fixture"})


if __name__ == "__main__":
    unittest.main()
