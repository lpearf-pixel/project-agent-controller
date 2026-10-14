#!/usr/bin/env python3
"""Opt-in single-task Codex loop. Trusted repositories only; not a security sandbox."""
import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

RUNTIME = Path(os.environ.get('PAC_CODEX_LOOP_DATA', str(Path.home() / '.local/share/pac-codex-loop'))).expanduser()
BLOCKED = ('.env', '.codex/', '.github/workflows/', '.git/', 'AGENTS.md')


def call(argv, cwd=None, timeout=20, env=None, stdin=None, logfile=None):
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                         start_new_session=True)
    try:
        output, _ = p.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        output, _ = p.communicate()
        raise RuntimeError(f'timeout after {timeout}s: {argv[0]}')
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        logfile.write_text(output[:2_000_000], encoding='utf-8')
    return p.returncode, output[:16000]


def git(repo, *args, timeout=20):
    code, out = call(['git', '-C', str(repo), *args], timeout=timeout)
    if code:
        raise RuntimeError(f'git {args[0]} failed: {out[:1200]}')
    return out.strip()


def save(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def changed(repo):
    # Include tracked changed/deleted/renamed files and new untracked files.
    tracked = git(repo, 'diff', '--name-only', 'HEAD').splitlines()
    others = git(repo, 'ls-files', '--others', '--exclude-standard').splitlines()
    return sorted(set(tracked + others))


def disallowed(paths):
    return [p for p in paths if any(p == s or p.startswith(s) for s in BLOCKED) or
            '/.env' in p or p.endswith(('.pem', '.key', '.p12'))]


def doctor():
    print('Python:', sys.version.split()[0])
    for name in ('git', 'codex', 'gh'):
        binary = shutil.which(name)
        print(f'{name}:', binary or 'MISSING')
    if shutil.which('codex'):
        code, output = call(['codex', 'login', 'status'], timeout=20)
        print('Codex login:', 'OK' if code == 0 else 'NOT READY', output[:120].replace('\n', ' '))
    if shutil.which('gh'):
        code, _ = call(['gh', 'auth', 'status'], timeout=20)
        print('GitHub login:', 'OK' if code == 0 else 'NOT READY')
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        if os.environ.get(key):
            # Do not log proxy credentials.
            print(f'{key}: configured')
    return 0 if shutil.which('git') and shutil.which('codex') else 2


def run(args):
    repo = args.repo.expanduser().resolve(strict=True)
    prompt_path = args.task.expanduser().resolve(strict=True)
    if not (repo / '.git').exists():
        # normal .git directory or linked worktree git file, both allowed
        raise RuntimeError('repo must be a Git checkout')
    if args.publish and not shutil.which('gh'):
        raise RuntimeError('gh is required for --publish')
    if not shutil.which('codex'):
        raise RuntimeError('codex is required')
    if not args.check:
        raise RuntimeError('provide at least one trusted --check command')
    if not (0 <= args.repairs <= 3):
        raise RuntimeError('--repairs must be 0..3')
    # Only explicitly approved commands, never shell=True.
    tests = [shlex.split(x) for x in args.check]
    if any(not t or t[0] in ('sh', 'bash', 'sudo', 'curl', 'wget', 'docker') for t in tests):
        raise RuntimeError('unsafe validator command; use direct executable')
    base_branch = git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if not re.fullmatch(r'[A-Za-z0-9._/-]+', base_branch):
        raise RuntimeError('invalid base branch')
    if args.publish and base_branch.startswith('auto/'):
        raise RuntimeError('cannot publish PR against auto branch')
    base_sha = git(repo, 'rev-parse', 'HEAD')
    task = prompt_path.read_text(encoding='utf-8')
    if not (10 <= len(task) <= 40000):
        raise RuntimeError('task must be 10..40000 characters')
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / 'controller.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another Codex task is already running')
        task_id = uuid.uuid4().hex[:12]
        branch = 'auto/codex-' + task_id
        run_dir = RUNTIME / 'runs' / task_id
        work = run_dir / 'work'
        run_dir.mkdir(parents=True)
        state_path = run_dir / 'state.json'
        state = {'task_id': task_id, 'status': 'PRECHECK', 'base_sha': base_sha,
                 'base_branch': base_branch, 'branch': branch, 'repairs': 0,
                 'repo': str(repo), 'worktree': str(work), 'checks': args.check}
        save(state_path, state)
        try:
            # Create fresh branch without editing the user's current branch.
            git(repo, 'worktree', 'add', '-b', branch, str(work), base_sha, timeout=45)
            for attempt in range(args.repairs + 1):
                state.update(status='RUNNING_CODEX' if attempt == 0 else 'REPAIRING', repairs=max(0, attempt))
                save(state_path, state)
                evidence = run_dir / f'codex-{attempt}.jsonl'
                prompt = task if attempt == 0 else (task + '\n\nValidator failed. Repair only the failing change.\n' + last_failure[-6000:])
                status, _ = call(['codex', 'exec', '--json', '--sandbox', 'workspace-write',
                                  '--ask-for-approval', 'never', '--cd', str(work), '-'],
                                 cwd=work, stdin=prompt, timeout=args.codex_timeout,
                                 logfile=evidence)
                if status:
                    raise RuntimeError(f'Codex exited {status}, see {evidence}')
                state['status'] = 'VALIDATING'
                save(state_path, state)
                bad = disallowed(changed(work))
                if bad:
                    state['status'] = 'BLOCKED'
                    state['reason'] = 'protected paths changed: ' + ', '.join(bad)
                    save(state_path, state)
                    break
                last_failure = ''
                for i, test in enumerate(tests):
                    # Minimal environment for test. Same Unix user: NOT full isolation.
                    test_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ')}
                    test_env['HOME'] = str(run_dir)
                    result, output = call(test, cwd=work, env=test_env, timeout=args.check_timeout,
                                          logfile=run_dir / f'test-{attempt}-{i}.log')
                    if result:
                        last_failure += f'CHECK {i} {test!r} exited {result}:\n{output[-2500:]}\n'
                post_check_bad = disallowed(changed(work))
                if post_check_bad:
                    state.update(status='BLOCKED', reason='protected paths changed by validator: ' + ', '.join(post_check_bad))
                    save(state_path, state)
                    break
                if not last_failure:
                    if not changed(work):
                        state.update(status='BLOCKED', reason='Codex made no changes')
                    else:
                        state['status'] = 'LOCAL_PASS'
                    save(state_path, state)
                    break
                state['last_failure'] = last_failure[:2000]
                save(state_path, state)
            else:
                state.update(status='FAILED', reason='repair budget exhausted')
                save(state_path, state)
            if state['status'] == 'LOCAL_PASS' and args.publish:
                publish(repo, work, state, state_path, run_dir, task)
        except Exception as ex:
            state.update(status='FAILED', reason=str(ex)[:2000])
            save(state_path, state)
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return 0 if state['status'] in ('LOCAL_PASS', 'PR_DRAFT') else 1


def publish(repo, work, state, state_path, run_dir, task):
    # Publishing is explicit opt-in and uses only the new auto/codex-... branch.
    state['status'] = 'PUBLISHING'
    save(state_path, state)
    git(work, 'add', '-A')
    git(work, 'commit', '-m', 'chore: automatic Codex task ' + state['task_id'])
    git(work, 'push', '--set-upstream', 'origin', 'HEAD:refs/heads/' + state['branch'], timeout=90)
    # Do not auto-merge. gh is called only after the local validation succeeds.
    body_path = run_dir / 'pr-body.md'
    body_path.write_text('Draft: automatically generated and locally validated. HUMAN REVIEW REQUIRED.\n\n' +
                         task[:2000] + '\n\nBase: ' + state['base_sha'] + '\n', encoding='utf-8')
    code, out = call(['gh', 'pr', 'create', '--draft', '--base', state['base_branch'],
                      '--head', state['branch'], '--title', 'Codex task ' + state['task_id'],
                      '--body-file', str(body_path)], cwd=work, timeout=90)
    if code:
        raise RuntimeError('push succeeded but PR creation failed: ' + out[:500])
    state['status'] = 'PR_DRAFT'
    state['pr_url'] = out.strip()
    save(state_path, state)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description='PAC opt-in Codex loop (trusted repos only)')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    r = sub.add_parser('run')
    r.add_argument('--repo', type=Path, required=True)
    r.add_argument('--task', type=Path, required=True)
    r.add_argument('--check', action='append', required=True)
    r.add_argument('--repairs', type=int, default=2)
    r.add_argument('--codex-timeout', type=int, default=1200)
    r.add_argument('--check-timeout', type=int, default=300)
    r.add_argument('--publish', action='store_true', help='explicit opt-in: push new branch and draft PR')
    args = p.parse_args()
    try:
        return doctor() if args.command == 'doctor' else run(args)
    except (ValueError, RuntimeError, OSError) as ex:
        print('ERROR:', ex, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
