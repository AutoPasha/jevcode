"""The public benchmark: Exercism exercises as used by aider's polyglot run.

Our own nine tasks were written by us, which is exactly the objection anyone
should raise about them. This script turns a well known public set into tasks
the comparison stand already understands, so the same agents can be scored on
problems nobody here chose.

    python3 bench/polyglot.py --languages python --out bench/polyglot-tasks
    python3 bench/polyglot.py --languages python --verify
    python3 bench/compare.py --tasks bench/polyglot-tasks --who jevcode

Each exercise becomes a directory with the stub solution, the tests, a TASK.txt
holding the exercise's own instructions, and a Makefile, which is all the stand
needs. The reference solution is kept outside the task directory, under .gold,
so `--verify` can prove the tests fail on the stub and pass on the reference
without ever showing the agent the answer.

Source: https://github.com/Aider-AI/polyglot-benchmark (Exercism, MIT).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = "https://github.com/Aider-AI/polyglot-benchmark.git"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "jevcode", "polyglot-benchmark")

# How a language's tests are run, and how its test files are recognised when
# .meta/config.json does not list them.
RUNNERS = {
    "python": {"test": "python3 -m pytest -q", "ext": ".py"},
    "javascript": {"test": "npx --no-install jest --silent", "ext": ".js"},
    "go": {"test": "go test ./...", "ext": ".go"},
    "rust": {"test": "cargo test --quiet", "ext": ".rs"},
    "cpp": {"test": "cmake -S . -B build >/dev/null && cmake --build build >/dev/null"
                    " && ./build/*", "ext": ".cpp"},
    "java": {"test": "./gradlew test --quiet", "ext": ".java"},
}


def source_tree(source: str) -> str:
    """The benchmark repository, cloned once into the cache if need be."""
    if source:
        return source
    if os.path.isdir(os.path.join(CACHE, ".git")):
        return CACHE
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    print("cloning %s" % UPSTREAM, file=sys.stderr)
    subprocess.run(["git", "clone", "--depth", "1", UPSTREAM, CACHE], check=True)
    return CACHE


def exercises(tree: str, language: str) -> list:
    root = os.path.join(tree, language, "exercises", "practice")
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root) if not n.startswith("."))


def meta(tree: str, language: str, name: str) -> dict:
    path = os.path.join(tree, language, "exercises", "practice", name,
                        ".meta", "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("files") or {}
    except (OSError, ValueError):
        return {}


def instructions(directory: str) -> str:
    """The exercise's own brief, in the order Exercism shows it."""
    parts = []
    for name in ("introduction.md", "instructions.md", "instructions.append.md"):
        path = os.path.join(directory, ".docs", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                parts.append(fh.read().strip())
    return "\n\n".join(p for p in parts if p)


def build_task(tree: str, language: str, name: str, out: str) -> str | None:
    """One exercise as a task directory; returns its name, or None if unusable."""
    src = os.path.join(tree, language, "exercises", "practice", name)
    files = meta(tree, language, name)
    solution = [f for f in files.get("solution") or [] if os.path.exists(os.path.join(src, f))]
    tests = [f for f in files.get("test") or [] if os.path.exists(os.path.join(src, f))]
    example = [f for f in files.get("example") or [] if os.path.exists(os.path.join(src, f))]
    if not solution or not tests or not example:
        return None

    task_name = "%s-%s" % (language, name)
    target = os.path.join(out, task_name)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)

    # Everything the exercise ships except its own answer: the stub, the tests,
    # and whatever support files sit alongside them (package.json, Cargo.toml).
    for entry in sorted(os.listdir(src)):
        if entry in (".meta", ".docs", ".exercism", ".git"):
            continue
        s, d = os.path.join(src, entry), os.path.join(target, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, d)

    brief = instructions(src)
    editable = ", ".join(solution)
    with open(os.path.join(target, "TASK.txt"), "w", encoding="utf-8") as fh:
        fh.write(brief + "\n\n"
                 "Make the tests pass. Edit only %s; the test files are fixed "
                 "and must not be changed.\n" % editable)

    with open(os.path.join(target, ".protected"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(tests) + "\n")

    runner = RUNNERS[language]["test"]
    with open(os.path.join(target, "Makefile"), "w", encoding="utf-8") as fh:
        fh.write("test:\n\t%s\n" % runner)

    gold = os.path.join(out, ".gold", task_name)
    os.makedirs(gold, exist_ok=True)
    with open(os.path.join(gold, "files.json"), "w", encoding="utf-8") as fh:
        json.dump({"solution": solution, "example": example, "test": tests}, fh)
    for wanted, has in zip(solution, example):
        shutil.copy2(os.path.join(src, has), os.path.join(gold, os.path.basename(wanted)))
    return task_name


def tests_pass(directory: str, timeout: int) -> bool:
    try:
        return subprocess.run(["make", "test"], cwd=directory, capture_output=True,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def verify(out: str, names: list, timeout: int) -> int:
    """A task only counts if the stub fails and the reference passes.

    Anything else means we would be scoring the harness, not the agent: a task
    that is green before anyone touches it hands every contestant a free point,
    and one that stays red under its own reference solution can never be won.
    """
    bad = 0
    for name in names:
        directory = os.path.join(out, name)
        gold = os.path.join(out, ".gold", name)
        with open(os.path.join(gold, "files.json"), encoding="utf-8") as fh:
            solution = json.load(fh)["solution"]
        before = tests_pass(directory, timeout)
        saved = {f: open(os.path.join(directory, f), "rb").read() for f in solution}
        for f in solution:
            shutil.copy2(os.path.join(gold, os.path.basename(f)),
                         os.path.join(directory, f))
        after = tests_pass(directory, timeout)
        for f, body in saved.items():
            with open(os.path.join(directory, f), "wb") as fh:
                fh.write(body)
        ok = after and not before
        if not ok:
            bad += 1
        print("%-34s stub %s  reference %s  %s" % (
            name, "fails" if not before else "PASSES",
            "passes" if after else "FAILS", "ok" if ok else "unusable"), flush=True)
    print("\n%d of %d usable" % (len(names) - bad, len(names)))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--languages", default="python",
                    help="comma separated; %s" % ", ".join(RUNNERS))
    ap.add_argument("--only", default="", help="only these exercises, comma separated")
    ap.add_argument("--limit", type=int, default=0, help="first N exercises per language")
    ap.add_argument("--source", default="", help="an existing clone of the benchmark")
    ap.add_argument("--out", default=os.path.join(HERE, "polyglot-tasks"))
    ap.add_argument("--verify", action="store_true",
                    help="check every built task: stub red, reference green")
    ap.add_argument("--timeout", type=int, default=120, help="per test run, seconds")
    args = ap.parse_args()

    languages = [l.strip() for l in args.languages.split(",") if l.strip()]
    unknown = [l for l in languages if l not in RUNNERS]
    if unknown:
        print("no runner for: %s" % ", ".join(unknown), file=sys.stderr)
        return 2

    tree = source_tree(args.source)
    wanted = {w.strip() for w in args.only.split(",") if w.strip()}
    built, skipped = [], []
    for language in languages:
        names = exercises(tree, language)
        if wanted:
            names = [n for n in names if n in wanted or "%s-%s" % (language, n) in wanted]
        if args.limit:
            names = names[:args.limit]
        for name in names:
            task = build_task(tree, language, name, args.out)
            (built if task else skipped).append(task or "%s-%s" % (language, name))

    print("built %d tasks in %s" % (len(built), args.out))
    if skipped:
        print("skipped %d without a stub, tests or reference: %s"
              % (len(skipped), ", ".join(skipped[:5])))
    if args.verify:
        return 1 if verify(args.out, built, args.timeout) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
