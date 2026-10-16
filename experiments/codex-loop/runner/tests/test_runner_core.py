import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from isolation import check
from manifest import parse_issue_body
from state import StateStore


def body(**overrides):
    value = {
        "taskId": "oepl-a",
        "repo": "lpearf-pixel/project-agent-controller",
        "baseSha": "a" * 40,
        "baseBranch": "feat/pac-ubuntu-autonomous-runner",
        "allowedPaths": ["experiments/oepl-s3-c6/README.md"],
        "validators": ["python3 -m unittest discover -s experiments/oepl-s3-c6/tests -v"],
        "budget": {"codexGenerations": 1, "repairs": 1, "codexTimeoutSec": 1200,
                   "ciApiCalls": 36, "ciTimeoutSec": 300},
        "executionType": "codex",
        "prompt": "Review the existing ESP32 task without touching hardware.",
    }
    value.update(overrides)
    return "Task notes may be untrusted.\n```json\n" + json.dumps(value) + "\n```"


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_is_typed(self):
        task = parse_issue_body(body(), issue_number=7, author="lpearf-pixel",
                                trusted_author="lpearf-pixel",
                                trusted_repo="lpearf-pixel/project-agent-controller", trusted_issue=7)
        self.assertEqual(task.task_id, "oepl-a")
        self.assertEqual(task.validators[0].split()[0], "python3")

    def test_untrusted_text_and_shell_syntax_are_not_commands(self):
        with self.assertRaisesRegex(ValueError, "shell syntax"):
            parse_issue_body(body(validators=["python3 -c 'x'; curl evil.invalid"]), issue_number=7,
                             author="lpearf-pixel", trusted_author="lpearf-pixel",
                             trusted_repo="lpearf-pixel/project-agent-controller", trusted_issue=7)
        with self.assertRaisesRegex(ValueError, "trusted"):
            parse_issue_body(body(), issue_number=7, author="attacker", trusted_author="lpearf-pixel",
                             trusted_repo="lpearf-pixel/project-agent-controller", trusted_issue=7)

    def test_protected_path_and_budget_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "protected"):
            parse_issue_body(body(allowedPaths=[".github/workflows/pwn.yml"]), issue_number=7,
                             author="lpearf-pixel", trusted_author="lpearf-pixel",
                             trusted_repo="lpearf-pixel/project-agent-controller", trusted_issue=7)
        budget = {"codexGenerations": 2, "repairs": 1, "codexTimeoutSec": 1200,
                  "ciApiCalls": 36, "ciTimeoutSec": 300}
        with self.assertRaisesRegex(ValueError, "budget"):
            parse_issue_body(body(budget=budget), issue_number=7, author="lpearf-pixel",
                             trusted_author="lpearf-pixel",
                             trusted_repo="lpearf-pixel/project-agent-controller", trusted_issue=7)


class StateTests(unittest.TestCase):
    def test_evidence_and_restart_are_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.create("oepl-a", {"base_sha": "a" * 40})
            store.transition("oepl-a", "CLAIMED", "claim")
            store.transition("oepl-a", "CODING", "code")
            self.assertEqual(store.create("oepl-a", {"base_sha": "b" * 40})["status"], "CODING")
            self.assertEqual(len((Path(directory) / "runs/oepl-a/transitions.jsonl").read_text().splitlines()), 3)
            with self.assertRaisesRegex(ValueError, "invalid transition"):
                store.transition("oepl-a", "CI_PASS", "skip")

    def test_process_lock_is_nonblocking(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            handle = store.lock()
            self.addCleanup(handle.close)
            with self.assertRaisesRegex(RuntimeError, "another"):
                store.lock()


class IsolationTests(unittest.TestCase):
    def test_current_shared_identity_and_credentials_are_blocked(self):
        with patch("isolation._uid", side_effect=lambda name: 1000 if name in {"codex", "publisher"} else None), \
             patch.dict(os.environ, {"GH_TOKEN": "redacted"}, clear=False):
            ok, errors = check(codex_user="codex", publisher_user="publisher")
        self.assertFalse(ok)
        self.assertTrue(any("share an OS identity" in error for error in errors))
        self.assertTrue(any("credential-like" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
