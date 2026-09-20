"""Run a whole benchmark a pair at a time, and survive being interrupted.

One agent on one task takes minutes, so a set of 34 exercises times two
contestants is hours — longer than any single command here is allowed to live.
This walks the pairs, writes each result into its own file under `bench/.runs`,
skips whatever is already there and stops when the time budget runs out. Run it
again and it picks up where it stopped; `bench/merge.py` builds the table.

    python3 bench/sweep.py --tasks bench/polyglot-tasks --who jevcode,opencode-minimax
    python3 bench/sweep.py --tasks bench/polyglot-tasks --who jevcode --budget 2400
    python3 bench/sweep.py --tasks bench/polyglot-tasks --who all --list
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import contestants, task_names  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def slot(runs: str, agent: str, task: str, attempt: int) -> str:
    return os.path.join(runs, "%s__%s__%d.json" % (agent, task, attempt))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default=os.path.join(HERE, "tasks"))
    ap.add_argument("--who", default="jevcode")
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--runs", default=os.path.join(HERE, ".runs"))
    ap.add_argument("--budget", type=int, default=2700,
                    help="seconds; stop before starting a pair that would overrun it")
    ap.add_argument("--timeout", type=int, default=600, help="per pair, seconds")
    ap.add_argument("--list", action="store_true", help="show what is left and exit")
    args, extra = ap.parse_known_args()

    everyone = contestants()
    who = list(everyone) if args.who == "all" else [w.strip() for w in args.who.split(",")]
    unknown = [w for w in who if w not in everyone]
    if unknown:
        print("no such contestant: %s" % ", ".join(unknown), file=sys.stderr)
        return 2

    os.makedirs(args.runs, exist_ok=True)
    names = task_names(args.only, args.tasks)
    pending = [(agent, task, attempt + 1)
               for task in names for agent in who for attempt in range(args.repeat)
               if not os.path.exists(slot(args.runs, agent, task, attempt + 1))]

    done = len(names) * len(who) * args.repeat - len(pending)
    print("%d pairs done, %d left" % (done, len(pending)))
    if args.list:
        for agent, task, attempt in pending:
            print("%-20s %s #%d" % (agent, task, attempt))
        return 0

    started, ran = time.time(), 0
    for agent, task, attempt in pending:
        left = args.budget - (time.time() - started)
        if left < args.timeout:
            print("budget spent, %d pairs still to go" % (len(pending) - ran), flush=True)
            break
        command = [sys.executable, os.path.join(HERE, "compare.py"),
                   "--tasks", args.tasks, "--only", task, "--who", agent,
                   "--timeout", str(args.timeout),
                   "--out", slot(args.runs, agent, task, attempt)] + extra
        subprocess.run(command)
        ran += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
