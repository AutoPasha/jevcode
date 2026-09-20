"""Same task, same repository, same test — several agents, one table.

Comparing coding agents honestly is mostly a matter of refusing to cheat: every
contestant gets an untouched copy of the same directory, the same sentence, the
same time limit, and is judged by the project's own tests rather than by anyone
reading the diff. No partial credit, no retries, no prompt tuned per agent.

    python3 bench/compare.py --who jevcode
    python3 bench/compare.py --who jevcode,opencode --repeat 3
    python3 bench/compare.py --results out.json --table

Contestants other than jevcode come from `bench/contestants.json`, where each
one is a command template with {dir} and {task} in it. That file is yours to
edit — the comparison is only as fair as the commands you put in it, and it is
better that you can read them than that they are hidden in here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")
CONTESTANTS = os.path.join(HERE, "contestants.json")


# ------------------------------------------------------------------- tasks

def task_names(only: str = "", tasks: str = TASKS) -> list:
    names = sorted(n for n in os.listdir(tasks)
                   if not n.startswith(".")
                   and os.path.exists(os.path.join(tasks, n, "TASK.txt")))
    if only:
        wanted = {w.strip() for w in only.split(",") if w.strip()}
        names = [n for n in names if n in wanted]
    return names


def task_text(name: str, tasks: str = TASKS) -> str:
    with open(os.path.join(tasks, name, "TASK.txt"), encoding="utf-8") as fh:
        return fh.read().strip()


def fresh_copy(name: str, tasks: str = TASKS) -> str:
    """An untouched copy of the task, without the caches a previous run left."""
    root = tempfile.mkdtemp(prefix="jevbench-%s-" % name)
    target = os.path.join(root, name)
    shutil.copytree(os.path.join(tasks, name), target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return target


def protected(directory: str) -> dict:
    """Checksums of the files a contestant is not allowed to touch.

    A task built from a public benchmark ships a `.protected` list naming its
    test files. Making tests pass by rewriting them is the oldest way to win a
    benchmark, and the only defence that costs nothing is to look afterwards."""
    listing = os.path.join(directory, ".protected")
    if not os.path.exists(listing):
        return {}
    out = {}
    with open(listing, encoding="utf-8") as fh:
        for line in fh:
            name = line.strip()
            path = os.path.join(directory, name)
            if name and os.path.exists(path):
                out[name] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return out


def tampered(directory: str, before: dict) -> list:
    """Which protected files came back different, if any."""
    after = protected(directory)
    return sorted(n for n in before if after.get(n) != before[n])


def tests_pass(directory: str, timeout: int = 180) -> bool:
    try:
        return subprocess.run(["make", "test"], cwd=directory, capture_output=True,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------- contestants

def contestants() -> dict:
    """jevcode is built in; everyone else is a command template on disk."""
    out = {"jevcode": {"kind": "builtin"}}
    if os.path.exists(CONTESTANTS):
        with open(CONTESTANTS, encoding="utf-8") as fh:
            for name, spec in (json.load(fh) or {}).items():
                out[name] = dict(spec, kind=spec.get("kind", "command"))
    return out


def run_builtin(directory: str, task: str, args) -> dict:
    """jevcode in-process: the same code path as `jevcode run`, minus the exec."""
    from jevcode.engine import Agent
    from jevcode.systemone import SystemOne, Usage
    from jevcode.trace import Trace
    from jevcode.writer import Writer, WriterUsage

    usage, writer_usage = Usage(), WriterUsage()
    one = SystemOne(usage=usage)
    writer = Writer(usage=writer_usage)
    agent = Agent(task, directory, one=one, writer=writer,
                  trace=Trace(quiet=True), max_steps=args.max_steps,
                  candidates=args.candidates, settle_at=args.settle_at)
    started = time.time()
    # Contestants are held to a time limit and our own agent was not, so one
    # task it circled on ate the whole run's budget and the stand stopped. The
    # clock has to run the same for everybody, including us.
    def ring(signum, frame):
        raise TimeoutError("timed out after %ds" % args.timeout)
    previous = signal.signal(signal.SIGALRM, ring)
    signal.alarm(max(1, int(args.timeout)))
    try:
        outcome = agent.run()
        note = outcome.reason
    except TimeoutError as ex:
        note = str(ex)
    except (RuntimeError, KeyboardInterrupt) as ex:
        note = str(ex)[:200]
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    same_money = (not writer_usage.currency
                  or writer_usage.currency == usage.currency)
    return {"seconds": round(time.time() - started, 2),
            # Jev and the writer can be billed by different providers in
            # different currencies; adding those up would invent an exchange
            # rate nobody agreed to, so they stay apart unless they match.
            "cost": round(usage.cost + (writer_usage.cost if same_money else 0.0), 6),
            "currency": usage.currency or writer_usage.currency,
            "writer_cost": 0.0 if same_money else round(writer_usage.cost, 6),
            "writer_currency": "" if same_money else writer_usage.currency,
            "decisions": usage.questions, "requests": usage.requests,
            "drafts": writer_usage.calls, "note": note}


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def tidy(text: str, directory: str) -> str:
    """The tail of an agent's output, without the colours or where it ran.

    The working copy lives in a temporary directory whose name says more about
    the machine that ran the benchmark than about the run, and results.json is
    meant to be committed."""
    text = ANSI.sub("", text).replace(directory, "<task dir>")
    text = text.replace(os.path.dirname(directory), "<tmp>")
    return " ".join(text.split())[-300:]


def quoted(task: str) -> str:
    """The task text, safe to drop inside the double quotes of a template.

    Our own tasks are one sentence each, but a public benchmark hands over a
    whole page of instructions with backticks, dollars and quotes in it. Left
    alone, the shell would read those as its own and the contestant would be
    handed a different task than jevcode got — or none at all."""
    for char in ("\\", '"', "$", "`"):
        task = task.replace(char, "\\" + char)
    return task


def run_command(directory: str, task: str, spec: dict, timeout: int) -> dict:
    command = spec["command"].replace("{dir}", directory).replace("{task}", quoted(task))
    started = time.time()
    env = dict(os.environ, **(spec.get("env") or {}))
    try:
        done = subprocess.run(command, shell=True, cwd=directory, env=env,
                              capture_output=True, timeout=timeout)
        note = tidy((done.stdout + done.stderr).decode("utf-8", "replace"), directory)
    except subprocess.TimeoutExpired:
        note = "timed out after %ds" % timeout
    except OSError as ex:
        note = str(ex)[:200]
    return {"seconds": round(time.time() - started, 2), "cost": 0.0, "currency": "",
            "decisions": 0, "requests": 0, "drafts": 0, "note": note}


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--who", default="jevcode",
                    help="comma separated; `all` for everyone in contestants.json")
    ap.add_argument("--only", default="", help="run only these tasks, comma separated")
    ap.add_argument("--tasks", default=TASKS,
                    help="a directory of task directories; the public set is built by bench/polyglot.py")
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per task, to see the variance")
    ap.add_argument("--timeout", type=int, default=600, help="per run, seconds")
    # A benchmark should measure the thing people install, so the knobs start
    # where the shipped defaults are. The old 14-step ceiling here was below
    # jevcode's own 24 and quietly cost us tasks the product would have had
    # more attempts at, while contestants were bounded only by the clock.
    from jevcode.config import DEFAULTS
    ap.add_argument("-n", "--candidates", type=int, default=DEFAULTS["candidates"])
    ap.add_argument("--max-steps", type=int, default=DEFAULTS["max_steps"])
    ap.add_argument("--settle-at", type=int, default=DEFAULTS["settle_at"])
    ap.add_argument("--out", default=os.path.join(HERE, "results.json"))
    ap.add_argument("--results", default="", help="skip running; read this file")
    ap.add_argument("--table", action="store_true", help="print the markdown table")
    args = ap.parse_args()

    if args.results:
        rows = json.load(open(args.results, encoding="utf-8"))["runs"]
        print(table(rows))
        return 0

    everyone = contestants()
    who = list(everyone) if args.who == "all" else [w.strip() for w in args.who.split(",")]
    unknown = [w for w in who if w not in everyone]
    if unknown:
        print("no such contestant: %s (have: %s)" % (", ".join(unknown),
                                                     ", ".join(everyone)), file=sys.stderr)
        return 2

    names = task_names(args.only, args.tasks)
    if not names:
        print("no tasks in %s" % args.tasks, file=sys.stderr)
        return 2

    rows = []
    for name in names:
        task = task_text(name, args.tasks)
        for agent in who:
            for attempt in range(args.repeat):
                directory = fresh_copy(name, args.tasks)
                before = tests_pass(directory)
                fixed = protected(directory)
                spec = everyone[agent]
                if spec["kind"] == "builtin":
                    result = run_builtin(directory, task, args)
                else:
                    result = run_command(directory, task, spec, args.timeout)
                after = tests_pass(directory)
                changed = tampered(directory, fixed)
                if changed:
                    result["note"] = ("edited the tests: %s; " % ", ".join(changed)
                                      + result.get("note", ""))[:300]
                row = dict(result, task=name, agent=agent, attempt=attempt + 1,
                           passed=bool(after and not before and not changed))
                rows.append(row)
                print("%-10s %-10s %s  %5.1fs  %s" % (
                    agent, name, "pass" if row["passed"] else "fail",
                    row["seconds"], row["note"][:60].replace("\n", " ")), flush=True)
                shutil.rmtree(os.path.dirname(directory), ignore_errors=True)

    payload = {"when": time.strftime("%Y-%m-%d %H:%M"), "runs": rows,
               "tasks": names, "agents": who}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("\nwrote %s" % args.out)
    print()
    print(table(rows))
    return 0


def summarise(rows: list) -> dict:
    """Per agent: how often it worked, how long it took, what it cost."""
    out: dict = {}
    for row in rows:
        entry = out.setdefault(row["agent"], {"runs": 0, "passed": 0, "seconds": [],
                                              "cost": 0.0, "currency": "",
                                              "writer_cost": 0.0, "writer_currency": "",
                                              "decisions": 0, "drafts": 0})
        entry["runs"] += 1
        entry["passed"] += 1 if row["passed"] else 0
        entry["seconds"].append(row["seconds"])
        entry["cost"] += row.get("cost") or 0.0
        entry["currency"] = entry["currency"] or (row.get("currency") or "")
        entry["writer_cost"] += row.get("writer_cost") or 0.0
        entry["writer_currency"] = (entry["writer_currency"]
                                    or (row.get("writer_currency") or ""))
        entry["decisions"] += row.get("decisions") or 0
        entry["drafts"] += row.get("drafts") or 0
    for entry in out.values():
        entry["median_seconds"] = round(statistics.median(entry["seconds"]), 1)
        entry["rate"] = round(100.0 * entry["passed"] / max(entry["runs"], 1))
    return out


def table(rows: list) -> str:
    totals = summarise(rows)
    lines = ["| agent | solved | median time | cost per task |",
             "| --- | --- | --- | --- |"]
    for agent, entry in sorted(totals.items(), key=lambda kv: -kv[1]["rate"]):
        money = ("%.2f %s" % (entry["cost"] / max(entry["runs"], 1), entry["currency"])
                 if entry["cost"] else "—")
        if entry.get("writer_cost"):
            money += " + %.4f %s" % (entry["writer_cost"] / max(entry["runs"], 1),
                                     entry["writer_currency"])
        lines.append("| %s | %d/%d (%d%%) | %.1fs | %s |"
                     % (agent, entry["passed"], entry["runs"], entry["rate"],
                        entry["median_seconds"], money))
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
