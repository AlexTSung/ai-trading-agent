#!/usr/bin/env python3
"""
version.py — the agent's version, and the tools to move between versions.

Why a trading agent needs this at all: a strategy change makes the performance
record before and after it incomparable. Phase 2 had to be declared void for
exactly that reason — the Judge's weights were changed, every score shifted by
up to 2.0 points, and "avg Judge >= 7.0" stopped meaning anything. Stamping a
version onto every journal and verdict makes results attributable to the ruleset
that produced them instead of to "the agent".

Usage:
  python3 version.py show                       # current version
  python3 version.py verify                     # VERSION == CLAUDE.md == CHANGELOG
  python3 version.py surface                    # files that constitute a version
  python3 version.py bump patch --reason "..."   # edit VERSION + CHANGELOG stub
  python3 version.py rollback 1.0.0             # DRY RUN — prints what would change
  python3 version.py rollback 1.0.0 --confirm    # actually restore
"""

import os
import re
import subprocess
import sys

ROOT           = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE   = os.path.join(ROOT, "VERSION")
CHANGELOG_FILE = os.path.join(ROOT, "CHANGELOG.md")
CLAUDE_FILE    = os.path.join(ROOT, "CLAUDE.md")

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# ── Reading the version ───────────────────────────────────────────────────────

def get_version() -> str:
    """
    Current version, or "0.0.0-unknown" if VERSION is missing or malformed.

    Never raises. This is called from the stamping path in every pipeline
    writer, and a version-file problem must not be able to stop a trading run —
    an unstamped journal is a smaller loss than a session that does not execute.
    """
    try:
        with open(VERSION_FILE) as f:
            text = f.read().strip()
    except OSError:
        return "0.0.0-unknown"
    return text if SEMVER_RE.match(text) else "0.0.0-unknown"


__version__ = get_version()


def parse(version: str) -> tuple:
    m = SEMVER_RE.match(version.strip().lstrip("v"))
    if not m:
        raise ValueError(f"Not a semver string: {version!r}")
    return tuple(int(g) for g in m.groups())


# ── The version surface ───────────────────────────────────────────────────────
# What a version IS. Rollback restores exactly this set and nothing else.
#
# The critical exclusion is logs/. Journals, verdicts, trade logs and round-trip
# records are the performance RECORD, not the strategy — 343 of them are tracked
# in git, so a plain `git checkout v1.0.0` would roll the audit trail back too
# and destroy the evidence you rolled back in order to examine.
#
# The one exception inside logs/ is learned parameters. A v1.0.0 rollback that
# left v1.4's learned Judge weights in place would not be v1.0.0 behaviour, so
# those ARE part of the surface.
VERSIONED_GLOBS = (
    "*.py",
    "tests/*.py",
    "CLAUDE.md",
    "watchlist.json",
    "forex_config.json",
    "routines.json",
    "journal-template.md",
    "requirements.txt",
    "*.sh",
    ".github/workflows/*.yml",
    "logs/learnings/*.json",       # learned parameters ARE strategy
    "VERSION",
    "CHANGELOG.md",
)


def _git(*args, check=True):
    return subprocess.run(["git", "-C", ROOT, *args], check=check,
                          capture_output=True, text=True)


def version_surface(ref: str = None) -> list:
    """
    Tracked files that constitute a version, sorted.

    Resolved against `ref` when given, so rollback can see files that exist in
    the target version but were deleted since.
    """
    args = ["ls-files"]
    if ref:
        args = ["ls-tree", "-r", "--name-only", ref]
    out = _git(*args).stdout.splitlines()

    import fnmatch
    keep = []
    for path in out:
        # logs/ is the performance record and is never restored, except learned
        # parameters, which are matched explicitly below.
        if path.startswith("logs/") and not path.startswith("logs/learnings/"):
            continue
        if any(fnmatch.fnmatch(path, g) for g in VERSIONED_GLOBS):
            keep.append(path)
    return sorted(keep)


# ── Consistency ───────────────────────────────────────────────────────────────

def declared_versions() -> dict:
    """Version as stated by each source of truth, for drift detection."""
    found = {"VERSION": get_version(), "CLAUDE.md": None, "CHANGELOG.md": None}

    try:
        with open(CLAUDE_FILE) as f:
            m = re.search(r"^>\s*\*\*Version:\*\*\s*v?(\d+\.\d+\.\d+)",
                          f.read(), re.MULTILINE)
            if m:
                found["CLAUDE.md"] = m.group(1)
    except OSError:
        pass

    try:
        with open(CHANGELOG_FILE) as f:
            m = re.search(r"^##\s*\[v?(\d+\.\d+\.\d+)\]", f.read(), re.MULTILINE)
            if m:
                found["CHANGELOG.md"] = m.group(1)
    except OSError:
        pass

    return found


def verify() -> tuple:
    """(ok, message). All three sources must agree."""
    found = declared_versions()
    missing = [k for k, v in found.items() if not v]
    if missing:
        return False, f"No version declared in: {', '.join(missing)}"
    if len(set(found.values())) != 1:
        detail = ", ".join(f"{k}={v}" for k, v in found.items())
        return False, f"Version drift — {detail}"
    return True, f"v{found['VERSION']} consistent across VERSION, CLAUDE.md, CHANGELOG.md"


# ── Bumping ───────────────────────────────────────────────────────────────────

def next_version(current: str, level: str) -> str:
    major, minor, patch = parse(current)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"level must be major, minor or patch — got {level!r}")


def bump(level: str, reason: str) -> str:
    """
    Write the new version to VERSION, CLAUDE.md and a CHANGELOG stub.

    Deliberately does NOT commit or tag. A version bump is a claim about what
    changed; it should be reviewed in a diff like any other change.
    """
    if not reason or not reason.strip():
        raise ValueError("A bump needs a reason — it goes in the CHANGELOG")

    current = get_version()
    new = next_version(current, level)

    with open(VERSION_FILE, "w") as f:
        f.write(new + "\n")

    with open(CLAUDE_FILE) as f:
        claude = f.read()
    claude = re.sub(r"^(>\s*\*\*Version:\*\*\s*)v?\d+\.\d+\.\d+",
                    rf"\g<1>v{new}", claude, count=1, flags=re.MULTILINE)
    with open(CLAUDE_FILE, "w") as f:
        f.write(claude)

    with open(CHANGELOG_FILE) as f:
        log = f.read()
    entry = (f"## [v{new}] — unreleased\n\n"
             f"**{level.upper()}** — {reason.strip()}\n\n"
             f"### Strategy / CLAUDE.md changes\n- \n\n"
             f"### Code changes\n- \n\n"
             f"### Comparability\n"
             f"- Does this invalidate the prior performance record? yes / no — why\n\n")
    marker = "<!-- versions below -->\n"
    log = log.replace(marker, marker + "\n" + entry, 1) if marker in log else log + "\n" + entry
    with open(CHANGELOG_FILE, "w") as f:
        f.write(log)

    return new


# ── Rollback ──────────────────────────────────────────────────────────────────

def rollback(target: str, confirm: bool = False) -> dict:
    """
    Restore the version surface from a tag or commit. Dry run unless confirmed.

    Restores code, CLAUDE.md, config and learned parameters. Does NOT touch
    logs/ — journals, verdicts and trade logs are the record of what happened
    and must survive the rollback that is meant to let you study them.

    Uncommitted changes are refused rather than overwritten: a rollback that
    silently discards work in progress is not recoverable.
    """
    ref = target if target.startswith("v") else f"v{target}"

    probe = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if probe.returncode != 0:
        probe = _git("rev-parse", "--verify", "--quiet", f"{target}^{{commit}}",
                     check=False)
        if probe.returncode != 0:
            return {"ok": False,
                    "error": f"No such tag or commit: {target}",
                    "available": _git("tag", "-l").stdout.split()}
        ref = target

    dirty = [l for l in _git("status", "--porcelain").stdout.splitlines() if l.strip()]
    if dirty and not confirm:
        pass  # reported below; a dry run is always safe to show
    elif dirty and confirm:
        return {"ok": False,
                "error": ("Working tree is not clean. Commit or stash first — "
                          "a rollback must not silently discard uncommitted work."),
                "dirty": dirty}

    paths = version_surface(ref)
    if not paths:
        return {"ok": False, "error": f"{ref} contains no versioned files"}

    diff = _git("diff", "--name-only", ref, "--", *paths, check=False).stdout.split()

    result = {
        "ok": True,
        "target": ref,
        "dry_run": not confirm,
        "files_in_surface": len(paths),
        "files_that_would_change": sorted(diff),
        "logs_preserved": True,
        "note": ("logs/ is excluded — journals, verdicts and trade logs are the "
                 "performance record, not the strategy. Learned parameters under "
                 "logs/learnings/ ARE restored, since stale learned weights would "
                 "not reproduce the target version's behaviour."),
    }
    if dirty:
        result["warning_uncommitted"] = dirty

    if not confirm:
        result["to_apply"] = f"python3 version.py rollback {target} --confirm"
        return result

    _git("checkout", ref, "--", *paths)
    result["restored"] = True
    result["now"] = get_version()
    result["next_step"] = ("Review `git status`, run the guardrail tests, then "
                           "commit the revert as its own change.")
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import json
    args = sys.argv[1:]
    action = args[0] if args else "show"

    if action == "show":
        print(get_version())

    elif action == "verify":
        ok, msg = verify()
        print(("OK — " if ok else "FAIL — ") + msg)
        sys.exit(0 if ok else 1)

    elif action == "surface":
        for p in version_surface():
            print(p)

    elif action == "bump":
        if len(args) < 2:
            print("usage: version.py bump major|minor|patch --reason \"...\"")
            sys.exit(2)
        reason = ""
        if "--reason" in args:
            i = args.index("--reason")
            reason = " ".join(args[i + 1:])
        try:
            new = bump(args[1], reason)
        except ValueError as e:
            print(f"error: {e}")
            sys.exit(2)
        print(f"Bumped to v{new}. Edit CHANGELOG.md, then commit and tag:")
        print(f"  git commit -am \"...\" && git tag -a v{new} -m \"v{new}\"")

    elif action == "rollback":
        if len(args) < 2:
            print("usage: version.py rollback <version> [--confirm]")
            sys.exit(2)
        out = rollback(args[1], confirm="--confirm" in args)
        print(json.dumps(out, indent=2))
        sys.exit(0 if out.get("ok") else 1)

    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
