#!/usr/bin/env python3
"""Transport locally completed PAC Runner edits from read-only Parallels share.

Dry-run by default. Only copies approved source files to an existing
Ubuntu-native Git checkout. Never commits, pushes, resets, runs Codex, or
touches the source .git. Conflicting remote changes fail closed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

TARGET_BRANCH = "feat/pac-ubuntu-autonomous-runner"
ALLOWED_PREFIXES = ("experiments/codex-loop/", "docs/architecture/")
BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".sqlite", ".db", ".pyc")
MAX_FILE_SIZE = 1_500_000


def git(root, *args, check=True):
    p = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=30,
    )
    if check and p.returncode:
        raise RuntimeError(
            "git " + str(args[0]) + " failed; inspect the checkout without resetting it"
        )
    return p.returncode, p.stdout


def base_blob(root, revision, path):
    code, contents = git(root, "show", revision + ":" + path, check=False)
    return contents if code == 0 else None


def allowed(path):
    parts = PurePosixPath(path).parts
    if (not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts)
            or not path.startswith(ALLOWED_PREFIXES)):
        return False
    if (any(p in (".git", ".codex", "AGENTS.md", "node_modules", "__pycache__")
            or p.startswith(".env") for p in parts)):
        return False
    if path.endswith(BLOCKED_SUFFIXES):
        return False
    if any(keyword in path.lower() for keyword in ("token", "credential", "password",
                                                   "secret", "private_key")):
        return False
    return True


def changes(source):
    _, tracked = git(source, "diff", "--name-only", "-z", "HEAD")
    _, extras = git(source, "ls-files", "--others", "--exclude-standard", "-z")
    names = sorted({
        x.decode("utf-8") for x in (tracked + extras).split(b"\0") if x
    })
    return names


def prepare(source, dest):
    source = source.resolve(strict=True)
    dest = dest.resolve(strict=True)
    if source == dest or source in dest.parents or dest in source.parents:
        raise RuntimeError("source and destination must be independent checkouts")
    if not (source / ".git").exists() or not (dest / ".git").exists():
        raise RuntimeError("both directories must be existing Git checkouts")
    source_sha = git(source, "rev-parse", "HEAD")[1].decode().strip()
    dest_sha = git(dest, "rev-parse", "HEAD")[1].decode().strip()
    branch = git(dest, "branch", "--show-current")[1].decode().strip()
    if branch != TARGET_BRANCH:
        raise RuntimeError("destination must be on the dedicated Runner branch")
    if git(dest, "merge-base", "--is-ancestor", source_sha, "HEAD", check=False)[0]:
        raise RuntimeError("source base is not an ancestor of destination; stop")

    # Never mix unrelated local edits or overwrite uncommitted target data.
    dirty_dest = git(dest, "status", "--porcelain", "-z", "--untracked-files=all")[1]
    if dirty_dest:
        raise RuntimeError("destination already contains local changes; stop")

    filenames = changes(source)
    if not filenames:
        raise RuntimeError("source contains no local changes to recover")
    rejects = []
    ignored_unrelated = []
    conflicts = []
    accepted = []
    for name in filenames:
        if not name.startswith(ALLOWED_PREFIXES):
            ignored_unrelated.append(name)
            continue
        if not allowed(name):
            rejects.append(name)
            continue
        src_file = source / name
        dst_file = dest / name
        if (src_file.is_symlink() or not src_file.is_file()
                or any(p.is_symlink() for p in src_file.parents if p != source
                       and source in p.parents)):
            rejects.append(name)
            continue
        if src_file.stat().st_size > MAX_FILE_SIZE:
            rejects.append(name)
            continue
        # Do not accidentally transport credential-like literals.
        data = src_file.read_bytes()
        if re.search(
            rb"(?:github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|"
            rb"sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)", data,
        ):
            rejects.append(name)
            continue
        original = base_blob(source, source_sha, name)
        destination_base = base_blob(dest, dest_sha, name)
        # New local file: remote must not already own that path.
        # Existing file: destination has to be unchanged since local base.
        if destination_base != original:
            conflicts.append(name)
            continue
        if (dst_file.is_symlink()
                or any(p.is_symlink() for p in dst_file.parents if p != dest
                       and dest in p.parents)
                or (dst_file.exists() and not dst_file.is_file())):
            conflicts.append(name)
            continue
        if dst_file.exists() and dst_file.read_bytes() != (destination_base or b""):
            conflicts.append(name)
            continue
        accepted.append(name)
    return {
        "source": str(source), "destination": str(dest),
        "source_sha": source_sha, "destination_sha": dest_sha,
        "accepted": accepted, "rejected": rejects,
        "ignored_unrelated": ignored_unrelated, "conflicts": conflicts,
    }


def migrate(source, dest, apply=False):
    plan = prepare(source, dest)
    if not plan["accepted"]:
        raise RuntimeError("no eligible Runner changes found; nothing copied")
    if plan["rejected"] or plan["conflicts"]:
        plan["status"] = "BLOCKED"
        print(json.dumps(plan, indent=2))
        raise RuntimeError("unsafe, missing, or conflicting local edits: nothing copied")
    if not apply:
        plan["status"] = "DRY_RUN"
    else:
        for name in plan["accepted"]:
            target = Path(plan["destination"]) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            # Refuse replacing symlinks appearing between inspection and copy.
            if target.is_symlink():
                raise RuntimeError("destination changed during transfer; manual review needed")
            incoming = (Path(plan["source"]) / name).read_bytes()
            tmp = target.with_name(target.name + ".pac-migrate-tmp")
            with tmp.open("xb") as fh:
                fh.write(incoming)
            os.replace(tmp, target)
        plan["status"] = "COPIED_UNCOMMITTED"
    print(json.dumps(plan, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Recover read-only PAC Runner local edits")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        return migrate(args.source, args.destination, args.apply)
    except (RuntimeError, OSError, UnicodeError, ValueError) as exc:
        print("ERROR:", str(exc)[:450], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
