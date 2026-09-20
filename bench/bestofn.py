"""How much does the judge add on top of the writer?

The writer produces N solutions for each HumanEval task, the tests say which of
them actually work, and Jev — which never sees the tests — says which one it
would have picked. Three numbers come out: what you get from one draft, what you
get when Jev picks among N, and the ceiling (was a working draft there at all).

    TYPESAFE_API_KEY=... JEVCODE_WRITER_KEY=... python3 bench/bestofn.py --tasks 80 --n 6

The dataset is downloaded from the HumanEval repository on first run. Candidates
are executed locally: run this in a container if that worries you, it is running
code a small model wrote.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevcode import questions                              # noqa: E402
from jevcode.systemone import SystemOne, Usage             # noqa: E402
from jevcode.writer import Writer, WriterUsage, extract_code  # noqa: E402

DATA_URL = "https://raw.githubusercontent.com/openai/human-eval/master/data/HumanEval.jsonl.gz"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HumanEval.jsonl")
LETTERS = "ABCDEFGHIJ"

PROMPT = ("Complete this Python function. Reply with the full function, including "
          "imports and signature, in a single ```python block and nothing else.\n\n%s")


def dataset(limit: int) -> list:
    if not os.path.exists(CACHE):
        raw = urllib.request.urlopen(DATA_URL, timeout=120).read()
        with open(CACHE, "wb") as fh:
            fh.write(gzip.decompress(raw))
    with open(CACHE) as fh:
        return [json.loads(line) for line in fh][:limit]


def passes(task: dict, code: str) -> bool:
    program = "\n".join([code, "", task["test"], "", "check(%s)" % task["entry_point"]])
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        return subprocess.run([sys.executable, path], capture_output=True,
                              timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False
    finally:
        os.unlink(path)


def judge(one: SystemOne, task: dict, drafts: list) -> str:
    """Which draft Jev would pick, seeing the spec and the code but never the tests.

    Duplicates are collapsed first, the way `jevcode.patch` does it. A Choice
    distributes one unit of probability across its options, so the same answer
    offered three times splits its own vote three ways and can lose to a single
    wrong one. Collapsing before judging is worth about ten points here.
    """
    unique, first = {}, []
    for draft in drafts:
        key = draft["code"].strip()
        if key in unique:
            continue
        unique[key] = draft["letter"]
        first.append(draft)
    state = {"specification": task["prompt"],
             "candidates": {d["letter"]: d["code"][:6000] for d in first}}
    if len(first) == 1:
        return first[0]["letter"]
    answers = one.ask(state, questions.judge_patches([d["letter"] for d in first],
                                                     task["prompt"]))
    return answers.pick("best")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=40)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    tasks = dataset(args.tasks)
    usage, writer_usage = Usage(), WriterUsage()
    one = SystemOne(usage=usage)
    writer = Writer(temperature=args.temperature, usage=writer_usage)
    started = time.time()

    def one_task(task: dict) -> dict:
        drafts = writer.drafts(PROMPT % task["prompt"], n=args.n, max_tokens=900)
        rows = [{"letter": LETTERS[i], "code": d.code, "ok": bool(d.code) and passes(task, d.code)}
                for i, d in enumerate(drafts)]
        alive = [r for r in rows if r["code"]]
        picked = judge(one, task, alive) if len(alive) > 1 else (alive[0]["letter"] if alive else "")
        chosen = next((r for r in rows if r["letter"] == picked), None)
        return {"first": rows[0]["ok"], "judged": bool(chosen and chosen["ok"]),
                "any": any(r["ok"] for r in rows)}

    results = []
    with cf.ThreadPoolExecutor(args.workers) as pool:
        for row in pool.map(one_task, tasks):
            results.append(row)
            done = len(results)
            print("\r%d/%d" % (done, len(tasks)), end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    n = len(results)
    rate = lambda key: 100.0 * sum(r[key] for r in results) / n      # noqa: E731
    print("writer: %s, %d tasks, %d drafts each" % (writer.model, n, args.n))
    print("one draft:            %5.0f%%" % rate("first"))
    print("Jev picks among %d:    %5.0f%%" % (args.n, rate("judged")))
    print("ceiling (any works):  %5.0f%%" % rate("any"))
    print("judging: %d questions in %d requests, %.1fs, %.4f %s"
          % (usage.questions, usage.requests, usage.seconds, usage.cost, usage.currency))
    print("writing: %d calls, %.1fs, %.4f %s"
          % (writer_usage.calls, writer_usage.seconds, writer_usage.cost,
             writer_usage.currency))
    print("wall clock: %.1fs" % (time.time() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
