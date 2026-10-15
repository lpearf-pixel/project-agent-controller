import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'loop.py'
DEFAULT_CHECK = "python3 -c 'import pathlib; assert pathlib.Path(\"result.txt\").exists()'"

class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-b','main',str(self.repo)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Local Test'],check=True)
        subprocess.run(
            ['git', '-C', str(self.repo), 'config', 'user.email', 'test@example.invalid'],
            check=True,
        )
        (self.repo/'README.md').write_text('hello\n')
        subprocess.run(['git','-C',str(self.repo),'add','README.md'],check=True)
        subprocess.run(
            ['git', '-C', str(self.repo), 'commit', '-m', 'init'],
            check=True,
            capture_output=True,
        )
        self.bin = self.root/'bin'
        self.bin.mkdir()
        codex = self.bin/'codex'
        codex.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib,json\n'
                         'if sys.argv[1:3] == ["login","status"]:\n'
                         '    print("Not logged in" if os.environ.get("FAKE_CODEX_NOAUTH") '
                         'else "Logged in using ChatGPT")\n'
                         '    sys.exit(1 if os.environ.get("FAKE_CODEX_NOAUTH") else 0)\n'
                         'prompt=sys.stdin.read()\n'
                         'if os.environ.get("FAKE_CODEX_REPAIR_ON_SECOND") '
                         'and "Validator failed" not in prompt:\n'
                         '    print(json.dumps({"type":"turn.completed"}))\n'
                         '    sys.exit(0)\n'
                         'if os.environ.get("FAKE_CODEX_SANDBOX_FAIL"):\n'
                         '    print(json.dumps({"type":"item.completed","item":'
                         '{"type":"file_change","status":"failed","changes":[]}}))\n'
                         '    print(json.dumps({"type":"item.completed","item":'
                         '{"type":"agent_message",'
                         '"text":"sandbox failed with a network-namespace error"}}))\n'
                         '    print(json.dumps({"type":"turn.completed"}))\n'
                         '    sys.exit(0)\n'
                         'target=pathlib.Path(os.getcwd())\n'
                         'name=os.environ.get("FAKE_CODEX_PATH","result.txt")\n'
                         '(target/name).write_text("done\\n")\n'
                         'print("{\\"type\\":\\"turn.completed\\"}")\n')
        codex.chmod(0o755)
        self.task = self.root/'task.md'
        self.task.write_text('Create a result file for this test case.\n')
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + ':' + os.environ['PATH'],
            PAC_CODEX_LOOP_DATA=str(self.root / 'data'),
        )

    def run_task(self, check=DEFAULT_CHECK):
        return subprocess.run([sys.executable, str(SCRIPT), 'run', '--repo',str(self.repo),
            '--task',str(self.task),'--check',check,'--repairs','1'],
            capture_output=True,text=True,env=self.env,timeout=30)

    def test_happy_path(self):
        run=self.run_task()
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('LOCAL_PASS',run.stdout)
        self.assertFalse((self.repo/'result.txt').exists())
        state = json.loads(run.stdout)
        shared_worktree = self.root / 'codex-worktrees' / state['task_id']
        self.assertEqual(Path(state['worktree']), shared_worktree)
        self.assertTrue((shared_worktree / 'result.txt').exists())

    def test_explicit_shared_worktree_root(self):
        custom_root = self.root / 'shared' / 'worktrees'
        run = subprocess.run(
            [sys.executable, str(SCRIPT), 'run', '--repo', str(self.repo),
             '--task', str(self.task), '--worktree-root', str(custom_root),
             '--check', DEFAULT_CHECK],
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        state = json.loads(run.stdout)
        self.assertEqual(Path(state['worktree']).parent, custom_root)

    def test_block_nested_worktree_root(self):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), 'run', '--repo', str(self.repo),
             '--task', str(self.task), '--worktree-root', str(self.repo / 'nested'),
             '--check', 'python3 -c "print(1)"'],
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertIn('outside the source repository', run.stderr)

    def test_noauth_blocks_before_creating_worktree(self):
        self.env['FAKE_CODEX_NOAUTH'] = '1'
        run = self.run_task()
        self.assertEqual(run.returncode, 2)
        self.assertIn('Codex login required', run.stderr)
        self.assertFalse((self.root / 'data' / 'runs').exists())

    def test_doctor_noauth_is_error(self):
        self.env['FAKE_CODEX_NOAUTH'] = '1'
        run = subprocess.run([sys.executable, str(SCRIPT), 'doctor'],
                             capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(run.returncode, 2)
        self.assertIn('NOT READY', run.stdout)

    def test_protected_path(self):
        self.env['FAKE_CODEX_PATH']='AGENTS.md'
        run=self.run_task()
        self.assertEqual(run.returncode,1)
        self.assertIn('BLOCKED',run.stdout)

    def test_sandbox_failure_blocks_immediately(self):
        self.env['FAKE_CODEX_SANDBOX_FAIL'] = '1'
        run = self.run_task()
        self.assertEqual(run.returncode, 1, run.stdout+run.stderr)
        state = json.loads(run.stdout)
        self.assertEqual(state['status'], 'BLOCKED')
        self.assertIn('file_change', state['reason'].lower())
        logs = list((self.root / 'data' / 'runs' / state['task_id']).glob('codex-*.jsonl'))
        self.assertEqual(
            len(logs), 1, 'infrastructure failure should never trigger repair attempts'
        )

    def test_successful_repair_clears_stale_failure(self):
        self.env['FAKE_CODEX_REPAIR_ON_SECOND'] = '1'
        run = self.run_task()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        state = json.loads(run.stdout)
        self.assertEqual(state['status'], 'LOCAL_PASS')
        self.assertEqual(state['validation'], 'PASS')
        self.assertEqual(state['validated_on_attempt'], 2)
        self.assertEqual(state['repairs'], 1)
        self.assertEqual(len(state['repair_history']), 1)
        self.assertNotIn('last_failure', state)
        self.assertIn('result.txt', state['changed_files'])

    def test_repair_exhaustion(self):
        run=self.run_task("python3 -c 'raise SystemExit(1)'")
        self.assertEqual(run.returncode,1)
        self.assertIn('FAILED',run.stdout)
        self.assertIn('repair budget exhausted',run.stdout)

    def test_input_invalid(self):
        run=self.run_task('bash -c echo')
        self.assertEqual(run.returncode,2)
        self.assertIn('unsafe validator',run.stderr)

class SmokeValidatorTests(unittest.TestCase):
    def test_rejects_no_file_or_empty_output(self):
        validator = SCRIPT.parent / 'verify-smoke.py'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = subprocess.run([sys.executable, str(validator)], cwd=root,
                                     capture_output=True, text=True, timeout=20)
            self.assertNotEqual(missing.returncode, 0)
            (root / 'hello.py').write_text('pass\n')
            empty = subprocess.run([sys.executable, str(validator)], cwd=root,
                                   capture_output=True, text=True, timeout=20)
            self.assertNotEqual(empty.returncode, 0)

    def test_accepts_correct_output(self):
        validator = SCRIPT.parent / 'verify-smoke.py'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'hello.py').write_text('print("hello")\n')
            good = subprocess.run([sys.executable, str(validator)], cwd=root,
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)


if __name__ == '__main__':
    unittest.main()
