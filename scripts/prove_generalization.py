"""Prove that new tasks were added without touching the agent or the auditor.

Purpose: `--freeze` records a SHA-256 of every file in agent/ and auditor/ (the code) and of the
company data that new tasks are allowed to change (company_pack/, evals/scenarios.yaml, the mock
company's seed data in apps/company/world.py). Run without flags later, it compares the current files with that
record and prints which code files changed (there should be none) and which data files changed
or were added (that is the whole cost of the new tasks). Exit code 0 means the code is
byte-identical to the freeze.
Why this design: the claim "a new task needs only new data" is checkable, not a promise. A hash
manifest needs no version-control tool, so the proof also works on a machine without git; when a
git repository with the tag `agent-frozen` exists, `git diff --stat agent-frozen` is shown too.
How it can fail: it proves the files are unchanged, not that they were unchanged for the right
reason (it cannot tell a reformat from a behaviour change); a manifest written after the code
was edited proves nothing, so --freeze must be run at the moment the code is frozen and the
manifest committed or backed up; line-ending conversion changes hashes (the script hashes
bytes with CRLF normalised to LF so a Windows checkout does not look like a change).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "evals/frozen_manifest.json"
CODE = ["agent", "auditor"]
DATA = ["company_pack", "evals/scenarios.yaml", "apps/company/world.py"]  # inputs/ is generated from world.py by the seed
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def _files(root: Path, entries: list[str]) -> list[Path]:
    found: list[Path] = []
    for entry in entries:
        path = root / entry
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found += [p for p in path.rglob("*") if p.is_file() and not SKIP_PARTS & set(p.parts) and p.suffix != ".pyc"]
    return sorted(found)


def _digest(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def snapshot(root: Path) -> dict[str, dict[str, str]]:
    def hashes(entries):
        return {p.relative_to(root).as_posix(): _digest(p) for p in _files(root, entries)}

    return {"code": hashes(CODE), "data": hashes(DATA)}


def compare(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    return {
        "changed": sorted(p for p in old if p in new and old[p] != new[p]),
        "added": sorted(p for p in new if p not in old),
        "removed": sorted(p for p in old if p not in new),
    }


def freeze(root: Path) -> Path:
    target = root / MANIFEST
    target.write_text(json.dumps(snapshot(root), indent=1, sort_keys=True), encoding="utf-8")
    return target


def _git_summary(root: Path) -> str | None:
    try:
        tag = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "agent-frozen"], cwd=root,
                             capture_output=True, text=True, timeout=20)
        if tag.returncode != 0:
            return None
        diff = subprocess.run(["git", "diff", "--stat", "agent-frozen", "--", *CODE], cwd=root,
                              capture_output=True, text=True, timeout=20)
        return diff.stdout.strip() or "(git agrees: no difference in agent/ and auditor/ since the tag agent-frozen)"
    except (OSError, subprocess.SubprocessError):
        return None


def report(root: Path) -> tuple[str, bool]:
    manifest = root / MANIFEST
    if not manifest.exists():
        return f"No freeze record at {MANIFEST}. Run `python -m scripts.prove_generalization --freeze` when the code is frozen.", False
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    now = snapshot(root)
    code, data = compare(frozen["code"], now["code"]), compare(frozen["data"], now["data"])
    unchanged = not any(code.values())
    lines = []
    if unchanged:
        lines.append(f"CODE UNCHANGED: all {len(frozen['code'])} files in {' and '.join(c + '/' for c in CODE)} are identical to the freeze.")
    else:
        lines.append("CODE CHANGED since the freeze (the generalization claim does NOT hold):")
        for kind, paths in code.items():
            lines += [f"  {kind}: {p}" for p in paths]
    changed_data = [(k, p) for k, paths in data.items() for p in paths]
    lines.append(f"DATA that changed or was added since the freeze ({len(changed_data)} files):" if changed_data
                 else "DATA: nothing changed since the freeze.")
    lines += [f"  {kind}: {path}" for kind, path in changed_data]
    git = _git_summary(root)
    if git:
        lines.append("git: " + git)
    return "\n".join(lines), unchanged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--freeze", action="store_true", help="record the current agent/ and auditor/ as the frozen state")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.freeze:
        target = freeze(args.root)
        print(f"Freeze recorded in {target.relative_to(args.root).as_posix()}")
        return 0
    text, unchanged = report(args.root)
    print(text)
    return 0 if unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
