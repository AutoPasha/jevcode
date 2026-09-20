"""Four small repositories, four failing test suites, one agent.

Each task in `bench/tasks/` is a working project with a feature missing and a
test that demands it. The agent is pointed at a copy of the directory and given
one sentence; afterwards the project's own tests decide whether it worked. No
partial credit.

    TYPESAFE_API_KEY=... JEVCODE_WRITER_KEY=... python3 bench/endtoend.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevcode.engine import Agent                  # noqa: E402
from jevcode.systemone import SystemOne, Usage    # noqa: E402
from jevcode.trace import Trace                   # noqa: E402
from jevcode.writer import Writer, WriterUsage    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")


def run_tests(directory: str) -> bool:
    try:
        return subprocess.run(["make", "test"], cwd=directory, capture_output=True,
                              timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="run one task by name")
    ap.add_argument("-n", "--candidates", type=int, default=6)
    ap.add_argument("--max-steps", type=int, default=14)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    names = sorted(n for n in os.listdir(TASKS)
                   if not n.startswith(".")
                   and os.path.exists(os.path.join(TASKS, n, "TASK.txt")))
    if args.only:
        names = [n for n in names if n == args.only]
    rows = []
    for name in names:
        source = os.path.join(TASKS, name)
        with open(os.path.join(source, "TASK.txt")) as fh:
            task = fh.read().strip()
        work = os.path.join(tempfile.mkdtemp(prefix="jevcode-"), name)
        shutil.copytree(source, work)
        os.remove(os.path.join(work, "TASK.txt"))

        usage, writer_usage = Usage(), WriterUsage()
        agent = Agent(task, work, one=SystemOne(usage=usage),
                      writer=Writer(usage=writer_usage),
                      trace=Trace(quiet=args.quiet), max_steps=args.max_steps,
                      candidates=args.candidates)
        print("\n=== %s: %s" % (name, task))
        started = time.time()
        try:
            outcome = agent.run()
            reason = outcome.reason
        except Exception as ex:                    # noqa: BLE001 - reported in the table
            reason = "crashed: %s" % str(ex)[:120]
        passed = run_tests(work)
        rows.append({"name": name, "passed": passed, "reason": reason,
                     "seconds": time.time() - started, "steps": agent.history,
                     "questions": usage.questions, "requests": usage.requests,
                     "jev_seconds": usage.seconds, "writer_calls": writer_usage.calls,
                     "tokens": usage.input_tokens})
        print("%s — %s" % ("tests pass" if passed else "TESTS FAIL", reason))

    print("\n%-10s %-6s %7s %7s %9s %8s" % ("task", "ok", "steps", "asked", "requests", "wall"))
    for row in rows:
        print("%-10s %-6s %7d %7d %9d %7.1fs"
              % (row["name"], "yes" if row["passed"] else "no", len(row["steps"]),
                 row["questions"], row["requests"], row["seconds"]))
    solved = sum(r["passed"] for r in rows)
    print("\n%d/%d solved · %d decisions in %d requests · %.0f tokens in"
          % (solved, len(rows), sum(r["questions"] for r in rows),
             sum(r["requests"] for r in rows), sum(r["tokens"] for r in rows)))
    return 0 if solved == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
