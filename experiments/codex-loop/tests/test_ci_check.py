"""CI gate never treats no checks, pending or failed checks as success."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_check


class CICheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.task_id = "123456789abc"
        self.repository = "test-owner/test-repo"
        self.url = "https://github.com/test-owner/test-repo/pull/42"
        root = self.data / "runs" / self.task_id
        root.mkdir(parents=True)
        state = {
            "status": "PR_DRAFT",
            "pr_url": self.url,
            "branch": "auto/codex-" + self.task_id,
            "base_branch": "main",
            "publish_commit": "a" * 40,
        }
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def do_check(self, rows, *, pr_draft=True):
        meta = {
            "isDraft": pr_draft,
            "headRefName": "auto/codex-" + self.task_id,
            "headRefOid": "a" * 40,
            "baseRefName": "main",
        }

        def fake_call(argv, **kwargs):
            if argv[1:3] == ["pr", "view"]:
                return 0, json.dumps(meta)
            if argv[1:3] == ["pr", "checks"]:
                return 0, json.dumps(rows)
            raise AssertionError(f"unexpected command: {argv}")

        with (
            patch.object(ci_check, "RUNTIME", self.data),
            patch.object(ci_check, "call", fake_call),
        ):
            return ci_check.check(self.task_id, self.repository)

    def test_full_pass(self):
        self.assertEqual(self.do_check([{"name": "a", "bucket": "pass", "state": "SUCCESS"}]), 0)

    def test_unknown_and_pending_never_pass(self):
        self.assertEqual(self.do_check([]), 1)
        self.assertEqual(self.do_check([{"name": "a", "bucket": "pending"}]), 1)
        self.assertEqual(self.do_check([{"name": "a", "bucket": "fail"}]), 1)

    def test_pr_must_match_draft(self):
        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
            self.do_check([{"bucket": "pass"}], pr_draft=False)


if __name__ == "__main__":
    unittest.main()
