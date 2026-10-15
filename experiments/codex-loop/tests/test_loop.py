import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'loop.py'

class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','-b','main',str(self.repo)],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.name','Local Test'],check=True)
        subprocess.run(['git','-C',str(self.repo),'config','user.email','test@example.invalid'],check=True)
        (self.repo/'README.md').write_text('hello\n')
        subprocess.run(['git','-C',str(self.repo),'add','README.md'],check=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-m','init'],check=True,capture_output=True)
        self.bin = self.root/'bin'
        self.bin.mkdir()
        codex = self.bin/'codex'
        codex.write_text('#!/usr/bin/env python3\nimport os,sys,pathlib\n'
                         'if sys.argv[1:3] == ["login","status"]: sys.exit(0)\n'
                         'prompt=sys.stdin.read()\n'
                         'target=pathlib.Path(os.getcwd())\n'
                         'name=os.environ.get("FAKE_CODEX_PATH","result.txt")\n'
                         '(target/name).write_text("done\\n")\n'
                         'print("{\\"type\\":\\"turn.completed\\"}")\n')
        codex.chmod(0o755)
        self.task = self.root/'task.md'
        self.task.write_text('Create a result file for this test case.\n')
        self.env = dict(os.environ, PATH=str(self.bin)+':'+os.environ['PATH'], PAC_CODEX_LOOP_DATA=str(self.root/'data'))

    def run_task(self, check="python3 -c 'import pathlib; assert pathlib.Path(\"result.txt\").exists()'"):
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
             '--check', "python3 -c 'import pathlib; assert pathlib.Path(\"result.txt\").exists()'"],
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

    def test_protected_path(self):
        self.env['FAKE_CODEX_PATH']='AGENTS.md'
        run=self.run_task()
        self.assertEqual(run.returncode,1)
        self.assertIn('BLOCKED',run.stdout)

    def test_repair_exhaustion(self):
        run=self.run_task("python3 -c 'raise SystemExit(1)'")
        self.assertEqual(run.returncode,1)
        self.assertIn('FAILED',run.stdout)
        self.assertIn('repair budget exhausted',run.stdout)

    def test_input_invalid(self):
        run=self.run_task('bash -c echo')
        self.assertEqual(run.returncode,2)
        self.assertIn('unsafe validator',run.stderr)

if __name__ == '__main__': unittest.main()
