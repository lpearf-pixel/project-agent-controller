"""CI gate tests, including older Ubuntu gh without 'pr checks --json'."""
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
        self.sha = "a" * 40
        root = self.data / "runs" / self.task_id
        root.mkdir(parents=True)
        state = {
            "status": "PR_DRAFT",
            "pr_url": self.url,
            "branch": "auto/codex-" + self.task_id,
            "base_branch": "main",
            "publish_commit": self.sha,
        }
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def job(self, status="completed", conclusion="success", sha=None):
        return {
            "head_sha": sha or self.sha,
            "name": "Test generated hello.py / verify",
            "status": status,
            "conclusion": conclusion,
        }

    def do_check(self, rows, *, statuses=None, pr_draft=True,
                 code_api=0, total_count=None):
        meta = {
            "isDraft": pr_draft,
            "headRefName": "auto/codex-" + self.task_id,
            "headRefOid": self.sha,
            "baseRefName": "main",
        }

        def fake_call(argv, **kwargs):
            if argv[1:3] == ["pr", "view"]:
                return 0, json.dumps(meta)
            if argv[1] == "api":
                if "/check-runs?" in argv[2]:
                    if code_api:
                        return 1, "gh api: authentication error"
                    return 0, json.dumps({
                        "total_count": len(rows) if total_count is None else total_count,
                        "check_runs": rows,
                    })
                if "/status?" in argv[2]:
                    return 0, json.dumps({"statuses": statuses or []})
            raise AssertionError(
                f"unsupported GH invocation (old CLI cannot use pr checks --json): {argv}"
            )

        with (
            patch.object(ci_check, "RUNTIME", self.data),
            patch.object(ci_check, "call", fake_call),
        ):
            return ci_check.check(self.task_id, self.repository)

    def test_success_with_real_actions_check_run(self):
        self.assertEqual(self.do_check([self.job()]), 0)

    def test_empty_skipped_and_pending_do_not_pass(self):
        self.assertEqual(self.do_check([]), 1)
        self.assertEqual(self.do_check([self.job(conclusion="skipped")]), 1)
        self.assertEqual(self.do_check([self.job(status="queued", conclusion=None)]), 1)
        self.assertEqual(self.do_check([self.job(status="in_progress", conclusion=None)]), 1)

    def test_failed_and_cancelled_do_not_pass(self):
        self.assertEqual(self.do_check([self.job(conclusion="failure")]), 1)
        self.assertEqual(self.do_check([self.job(conclusion="cancelled")]), 1)
        self.assertEqual(self.do_check([
            self.job(), self.job(conclusion="failure")]), 1)

    def test_unexpected_head_and_truncated_result_do_not_pass(self):
        self.assertEqual(self.do_check([self.job(sha="b" * 40)]), 1)
        self.assertEqual(self.do_check([self.job()], total_count=101), 1)
        self.assertEqual(self.do_check([self.job()], total_count=2), 1)

    def test_legacy_commit_status_checked(self):
        self.assertEqual(self.do_check([self.job()], statuses=[
            {"context": "external/build", "state": "success"}]), 0)
        self.assertEqual(self.do_check([self.job()], statuses=[
            {"context": "external/build", "state": "failure"}]), 1)
        self.assertEqual(self.do_check([self.job()], statuses=[
            {"context": "external/build", "state": "pending"}]), 1)

    def test_api_permission_error_is_diagnostic_not_json_parse(self):
        with self.assertRaisesRegex(RuntimeError, "gh api, exit 1"):
            self.do_check([self.job()], code_api=1)

    def test_pr_must_match_draft(self):
        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
            self.do_check([self.job()], pr_draft=False)


if __name__ == "__main__":
    unittest.main()
