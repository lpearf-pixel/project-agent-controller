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
import uuid
from pathlib import Path, PurePosixPath

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
    except KeyboardInterrupt:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise
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


def disallowed(paths, repo=None):
    bad = []
    for p in paths:
        parts = PurePosixPath(p).parts
        if (any(p == s or p.startswith(s) for s in BLOCKED) or
            any(s == 'AGENTS.md' or s == '.codex' or s.startswith('.env') for s in parts) or
            p.endswith(('.pem', '.key', '.p12', '.pyc', '.sqlite', '.db')) or
            (repo is not None and (repo / p).is_symlink())):
            bad.append(p)
    return bad


def codex_auth_ready():
    if not shutil.which('codex'):
        return False, 'Codex CLI missing'
    try:
        code, output = call(['codex', 'login', 'status'], timeout=15)
    except RuntimeError as ex:
        return False, str(ex)
    return code == 0 and 'logged in' in output.lower() and 'not logged in' not in output.lower(), output.strip()[:160]


def doctor():
    print('Python:', sys.version.split()[0])
    for name in ('git', 'codex', 'gh'):
        binary = shutil.which(name)
        print(f'{name}:', binary or 'MISSING')
    auth_ok, auth_message = codex_auth_ready()
    print('Codex login:', 'OK' if auth_ok else 'NOT READY', auth_message.replace('\n', ' '))
    if not auth_ok:
        print('ACTION: run codex login --device-auth in this Ubuntu user session.')
    if shutil.which('gh'):
        code, _ = call(['gh', 'auth', 'status'], timeout=20)
        print('GitHub login:', 'OK' if code == 0 else 'NOT READY')
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        if os.environ.get(key):
            # Do not log proxy credentials.
            print(f'{key}: configured')
    return 0 if shutil.which('git') and auth_ok else 2



def codex_result(logfile):
    """Check structured Codex events; process exit 0 is not proof of a successful edit."""
    seen_completed = False
    failures = []
    sandbox_failure = False
    if not logfile.is_file():
        return False, 'Codex event log missing'
    with logfile.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Codex stderr and startup messages share this output stream
            event_type = event.get('type')
            if event_type == 'turn.completed':
                seen_completed = True
            if event_type in ('turn.failed', 'turn.cancelled', 'error'):
                failures.append(str(event.get('message') or event.get('error') or event_type))
            item = event.get('item')
            if isinstance(item, dict):
                if item.get('type') == 'file_change' and item.get('status') == 'failed':
                    failures.append('Codex file_change failed')
                if item.get('type') == 'command_execution' and item.get('exit_code') not in (None, 0):
                    command_output = str(item.get('aggregated_output', ''))
                    if any(marker in command_output.lower() for marker in
                           ('bwrap:', 'rtm_newaddr', 'network namespace',
                            'network-namespace', 'sandbox failed')):
                        failures.append('Codex sandbox command execution failed')
            combined = json.dumps(event, ensure_ascii=False).lower()
            if any(marker in combined for marker in
                   ('network-namespace error', 'bwrap:', 'rtm_newaddr',
                    'sandbox failed', 'permission denied to create new namespace')):
                sandbox_failure = True
    if sandbox_failure:
        return False, 'Linux sandbox/namespace initialization failed; repair bubblewrap/AppArmor and rerun a new task'
    if failures:
        return False, '; '.join(dict.fromkeys(failures))[:400]
    if not seen_completed:
        return False, 'Codex did not emit turn.completed'
    return True, 'completed'


def run(args):
    repo = args.repo.expanduser().resolve(strict=True)
    prompt_path = args.task.expanduser().resolve(strict=True)
    if not (repo / '.git').exists():
        # normal .git directory or linked worktree git file, both allowed
        raise RuntimeError('repo must be a Git checkout')
    if args.publish and not shutil.which('gh'):
        raise RuntimeError('gh is required for --publish')
    auth_ok, auth_message = codex_auth_ready()
    if not auth_ok:
        raise RuntimeError('Codex login required before running: ' + auth_message +
                           '. Run codex login --device-auth in Ubuntu.')
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
    # Keep source worktrees accessible to both macOS and the Parallels VM.
    # Control state and the fcntl lock remain under RUNTIME on native Ubuntu storage.
    worktree_root = (args.worktree_root or (repo.parent / 'codex-worktrees')).expanduser().resolve()
    if worktree_root == repo or repo in worktree_root.parents:
        raise RuntimeError('worktree root must be outside the source repository')
    worktree_root.mkdir(parents=True, exist_ok=True)
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
        work = worktree_root / task_id
        run_dir.mkdir(parents=True)
        state_path = run_dir / 'state.json'
        state = {'task_id': task_id, 'status': 'PRECHECK', 'base_sha': base_sha,
                 'base_branch': base_branch, 'branch': branch, 'repairs': 0,
                 'repo': str(repo), 'worktree': str(work), 'checks': args.check,
                 'validation': 'PENDING', 'repair_history': []}
        save(state_path, state)
        try:
            # Create fresh branch without editing the user's current branch.
            git(repo, 'worktree', 'add', '-b', branch, str(work), base_sha, timeout=45)
            for attempt in range(args.repairs + 1):
                state.update(status='RUNNING_CODEX' if attempt == 0 else 'REPAIRING', repairs=max(0, attempt))
                save(state_path, state)
                evidence = run_dir / f'codex-{attempt}.jsonl'
                print(f'[codex-loop] Codex attempt {attempt + 1}/{args.repairs + 1} started; '
                      f'timeout={args.codex_timeout}s; worktree={work}', file=sys.stderr, flush=True)
                prompt = task if attempt == 0 else (task + '\n\nValidator failed. Repair only the failing change.\n' + last_failure[-6000:])
                status, _ = call(['codex', '--ask-for-approval', 'never', 'exec', '--json',
                                  '--sandbox', 'workspace-write', '--cd', str(work), '-'],
                                 cwd=work, stdin=prompt, timeout=args.codex_timeout,
                                 logfile=evidence)
                if status:
                    raise RuntimeError(f'Codex exited {status}, see {evidence}')
                successful, explanation = codex_result(evidence)
                if not successful:
                    state.update(status='BLOCKED', reason=explanation,
                                 evidence=str(evidence))
                    save(state_path, state)
                    break
                state['status'] = 'VALIDATING'
                save(state_path, state)
                bad = disallowed(changed(work), work)
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
                post_check_bad = disallowed(changed(work), work)
                if post_check_bad:
                    state.update(status='BLOCKED', reason='protected paths changed by validator: ' + ', '.join(post_check_bad))
                    save(state_path, state)
                    break
                if not last_failure:
                    if not changed(work):
                        state.update(status='BLOCKED', validation='BLOCKED',
                                     reason='Codex made no changes')
                    else:
                        state.update(status='LOCAL_PASS', validation='PASS',
                                     validated_on_attempt=attempt + 1,
                                     changed_files=changed(work))
                        # Earlier validator failures are historical, not the final result.
                        state.pop('last_failure', None)
                        state.pop('reason', None)
                    save(state_path, state)
                    break
                state['validation'] = 'FAIL'
                state['last_failure'] = last_failure[:2000]
                state['repair_history'].append(
                    {'attempt': attempt + 1, 'failure': last_failure[:1200]})
                save(state_path, state)
            else:
                state.update(status='FAILED', validation='FAIL',
                             reason='repair budget exhausted')
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
    r.add_argument('--worktree-root', type=Path, help='location for generated code; defaults to repo sibling codex-worktrees')
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
