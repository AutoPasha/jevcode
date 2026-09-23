"""Where the seconds of a step actually go.

A step is four different kinds of waiting and they are easy to confuse: asking
System One (many questions, one request, fast), writing drafts (one model call
per draft, run in parallel, slow), running the project's own tests (once per
candidate now, all at the same time), and everything else the agent does on
your machine. Only the last two are free, and they are usually the ones blamed.

The numbers here are wall clock, not sums: drafts are written in parallel, so
adding up per-call durations would invent seconds that never passed.

    python3 bench/profile.py --task slugify
    python3 bench/profile.py --dir path/to/repo "make the parser accept tabs"
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import fresh_copy, task_text  # noqa: E402


class Clock:
    """Wall time spent inside a named phase, however many calls it took."""

    def __init__(self) -> None:
        self.spent: dict = {}
        self.calls: dict = {}
        self.depth: dict = {}

    @contextlib.contextmanager
    def phase(self, name: str):
        # A phase inside a phase of the same name is the same wall clock seen
        # twice — the parallel test runs happen inside the race that started
        # them — and counting both would invent seconds that never passed.
        inside = self.depth.get(name, 0)
        self.depth[name] = inside + 1
        started = time.time()
        try:
            yield
        finally:
            self.depth[name] = inside
            if not inside:
                self.spent[name] = self.spent.get(name, 0.0) + (time.time() - started)
            self.calls[name] = self.calls.get(name, 0) + 1


def instrument(clock: Clock) -> None:
    """Wrap the two calls that leave the machine, keeping the code untouched."""
    from jevcode.systemone import SystemOne
    from jevcode.writer import Writer

    ask, drafts = SystemOne.ask, Writer.drafts

    def timed_ask(self, state, questions):
        with clock.phase("system one"):
            return ask(self, state, questions)

    def timed_drafts(self, prompt, *a, **kw):
        with clock.phase("writer"):
            return drafts(self, prompt, *a, **kw)

    SystemOne.ask, Writer.drafts = timed_ask, timed_drafts

    # The project's own tests, counted apart from the rest of the machine: the
    # agent now runs every candidate at once instead of one after another, and
    # a phase that changed that much should be visible rather than hidden
    # inside "everything else".
    from jevcode import act, tryout

    run_command, race, pipeline = act.run, tryout.race, tryout.pipeline

    def timed_run(command, cwd, *a, **kw):
        with clock.phase("tests"):
            return run_command(command, cwd, *a, **kw)

    def timed_race(*a, **kw):
        with clock.phase("tests"):
            return race(*a, **kw)

    # The pipeline is deliberately not two phases: the drafts are being written
    # while the tests run, so splitting it would either double-count the
    # overlap or hide it. One phase, named for what it actually is.
    def timed_pipeline(*a, **kw):
        # The runs started inside it are the same wall clock seen twice, so
        # they are suppressed the same way a race suppresses its own runs.
        clock.depth["tests"] = clock.depth.get("tests", 0) + 1
        try:
            with clock.phase("drafts + tests"):
                return pipeline(*a, **kw)
        finally:
            clock.depth["tests"] -= 1

    act.run, tryout.race, tryout.pipeline = timed_run, timed_race, timed_pipeline
    # engine imported `act` as a module, so patching the attribute is enough;
    # tryout calls act.run through the module for the same reason.


def run(directory: str, task: str, args) -> dict:
    from jevcode.engine import Agent
    from jevcode.systemone import SystemOne, Usage
    from jevcode.trace import Trace
    from jevcode.writer import Writer, WriterUsage

    clock = Clock()
    instrument(clock)
    usage, writer_usage = Usage(), WriterUsage()
    agent = Agent(task, directory,
                  one=SystemOne(usage=usage), writer=Writer(usage=writer_usage),
                  trace=Trace(quiet=True), max_steps=args.max_steps,
                  candidates=args.candidates, settle_at=args.settle_at)
    started = time.time()
    outcome = agent.run()
    total = time.time() - started

    one_s = clock.spent.get("system one", 0.0)
    writer_s = clock.spent.get("writer", 0.0)
    tests_s = clock.spent.get("tests", 0.0)
    both_s = clock.spent.get("drafts + tests", 0.0)
    return {
        "task": task[:70],
        "outcome": outcome.reason,
        "total": round(total, 2),
        "phases": {
            "system one": {"seconds": round(one_s, 2), "calls": clock.calls.get("system one", 0),
                           "questions": usage.questions},
            "writer": {"seconds": round(writer_s, 2), "calls": clock.calls.get("writer", 0),
                       "drafts": writer_usage.calls},
            "tests": {"seconds": round(tests_s, 2), "calls": clock.calls.get("tests", 0)},
            # One phase because the two overlap on purpose: a draft is tested
            # while the next one is still being written.
            "drafts + tests": {"seconds": round(both_s, 2),
                               "calls": clock.calls.get("drafts + tests", 0),
                               "drafts": writer_usage.calls},
            "machine": {"seconds": round(total - one_s - writer_s - tests_s - both_s, 2),
                        "calls": 0},
        },
        "cost": round(usage.cost + writer_usage.cost, 4),
        "currency": usage.currency or writer_usage.currency,
    }


def report(result: dict) -> str:
    lines = ["%s — %.1fs total" % (result["outcome"], result["total"])]
    for name, phase in result["phases"].items():
        share = 100.0 * phase["seconds"] / max(result["total"], 0.001)
        extra = ""
        if name == "system one":
            extra = "  %d requests, %d questions" % (phase["calls"], phase["questions"])
        elif name == "writer":
            extra = "  %d rounds, %d drafts" % (phase["calls"], phase["drafts"])
        elif name == "tests":
            extra = "  %d runs of the project's own command" % phase["calls"]
        elif name == "drafts + tests":
            if not phase["seconds"]:
                continue
            extra = "  %d writing steps, %d drafts, tested as they arrived" % (
                phase["calls"], phase["drafts"])
        lines.append("  %-11s %6.1fs  %3.0f%%%s" % (name, phase["seconds"], share, extra))
    lines.append("  cost        %6.2f %s" % (result["cost"], result["currency"]))
    return "\n".join(lines)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sentence", nargs="?", default="", help="the task, when using --dir")
    ap.add_argument("--task", default="", help="a task from bench/tasks instead")
    ap.add_argument("--dir", default="", help="a repository of your own")
    # The stand measures the product's own limits: a ceiling invented here
    # would only ever make our own numbers look worse than the thing we ship.
    from jevcode.config import DEFAULTS
    ap.add_argument("-n", "--candidates", type=int, default=DEFAULTS["candidates"])
    ap.add_argument("--max-steps", type=int, default=DEFAULTS["max_steps"])
    ap.add_argument("--settle-at", type=int, default=DEFAULTS["settle_at"])
    ap.add_argument("--out", default="", help="also write the numbers here as json")
    args = ap.parse_args()

    temporary = ""
    if args.task:
        directory = temporary = fresh_copy(args.task)
        sentence = task_text(args.task)
    elif args.dir:
        directory, sentence = args.dir, args.sentence
        if not sentence:
            print("with --dir you also need the task sentence", file=sys.stderr)
            return 2
    else:
        print("give --task <name> or --dir <path> with a sentence", file=sys.stderr)
        return 2

    try:
        result = run(directory, sentence, args)
    finally:
        if temporary:
            shutil.rmtree(os.path.dirname(temporary), ignore_errors=True)

    print(report(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
