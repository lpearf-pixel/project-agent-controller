"""CI gate tests, including older Ubuntu gh without 'pr checks --json'."""
import fcntl
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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

    def monitor_call(self, rows, *, statuses=None, head_sha=None, api_error=False,
                     max_attempts=3, max_api_calls=9):
        meta = {
            "isDraft": True,
            "headRefName": "auto/codex-" + self.task_id,
            "headRefOid": head_sha or self.sha,
            "baseRefName": "main",
        }
        calls = []

        def fake_call(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["pr", "view"]:
                return 0, json.dumps(meta)
            if argv[1] == "api":
                if api_error:
                    return 1, "authentication error"
                if "/check-runs?" in argv[2]:
                    return 0, json.dumps({"total_count": len(rows), "check_runs": rows})
                if "/status?" in argv[2]:
                    return 0, json.dumps({"statuses": statuses or []})
            raise AssertionError(argv)

        output = io.StringIO()
        with (patch.object(ci_check, "RUNTIME", self.data),
              patch.object(ci_check, "call", fake_call),
              redirect_stdout(output)):
            result = ci_check.monitor(self.task_id, self.repository,
                                      max_attempts=max_attempts,
                                      max_api_calls=max_api_calls,
                                      timeout_seconds=10, poll_interval=0)
        state = json.loads((self.data / "runs" / self.task_id / "state.json").read_text())
        return result, state, calls, json.loads(output.getvalue())

    def test_monitor_success_and_idempotent_repeat(self):
        result, state, calls, _output = self.monitor_call([self.job()])
        self.assertEqual(result, 0)
        self.assertEqual(state["ci_status"], "PASS")
        self.assertEqual(state["ci_head_sha"], self.sha)
        result, repeated, calls, _ = self.monitor_call([self.job()])
        self.assertEqual(result, 0)
        self.assertEqual(repeated["ci_status"], "PASS")
        self.assertEqual(len(calls), 0, "terminal observation must be idempotent")

    def test_monitor_failure(self):
        result, state, _calls, _output = self.monitor_call([self.job(conclusion="failure")])
        self.assertEqual(result, 1)
        self.assertEqual(state["ci_status"], "FAIL")

    def test_monitor_pending_exhausts_to_timeout(self):
        result, state, calls, _output = self.monitor_call(
            [self.job(status="queued", conclusion=None)], max_attempts=2, max_api_calls=6)
        self.assertEqual(result, 1)
        self.assertEqual(state["ci_status"], "TIMEOUT")
        self.assertEqual(state["ci_attempts"], 2)
        self.assertEqual(len(calls), 6)

    def test_monitor_api_error_is_unknown(self):
        result, state, calls, _output = self.monitor_call([], api_error=True)
        self.assertEqual(result, 1)
        self.assertEqual(state["ci_status"], "UNKNOWN")
        self.assertEqual(state["ci_observations"][0]["outcome"], "UNKNOWN")
        self.assertEqual(len(calls), 2)  # PR metadata plus the failed API request

    def test_monitor_missing_checks_is_unknown(self):
        result, state, _calls, _output = self.monitor_call([])
        self.assertEqual(result, 1)
        self.assertEqual(state["ci_status"], "UNKNOWN")

    def test_monitor_records_exact_api_call_counts(self):
        result, state, calls, _output = self.monitor_call([self.job()])
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(state["ci_api_calls"], 3)
        self.assertEqual(state["ci_observations"][0]["api_calls"], 3)

    def test_monitor_does_not_under_count_partial_api_failure(self):
        result, state, calls, _output = self.monitor_call([], api_error=True)
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["ci_api_calls"], 2)

    def test_sha_mismatch_uses_only_one_request(self):
        result, state, calls, _output = self.monitor_call([self.job()], head_sha="b" * 40)
        self.assertEqual(result, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["ci_api_calls"], 1)

    def test_monitor_rejects_concurrent_writer(self):
        lockfile = self.data / "controller.lock"
        with lockfile.open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                patch.object(ci_check, "RUNTIME", self.data),
                self.assertRaisesRegex(RuntimeError, "task is running"),
            ):
                ci_check.monitor(self.task_id, self.repository, poll_interval=0)

    def test_monitor_resume_never_resets_request_budget(self):
        _, state, _calls, _output = self.monitor_call(
            [self.job(status="queued", conclusion=None)],
            max_attempts=2, max_api_calls=3,
        )
        self.assertEqual(state["ci_status"], "TIMEOUT")
        self.assertEqual(state["ci_api_calls"], 3)
        # Terminal reruns are read-only regardless of the caller's polling args.
        result, repeated, calls, _output = self.monitor_call(
            [self.job()], max_attempts=2, max_api_calls=3,
        )
        self.assertEqual(result, 1)
        self.assertEqual(repeated["ci_api_calls"], 3)
        self.assertEqual(calls, [])

    def test_monitor_stops_on_sha_mismatch_without_check_api_calls(self):
        result, state, calls, _output = self.monitor_call([self.job()], head_sha="b" * 40)
        self.assertEqual(result, 1)
        self.assertEqual(state["ci_status"], "UNKNOWN")
        self.assertEqual(state["ci_reason"], "PR head SHA changed unexpectedly")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
