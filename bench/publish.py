"""Put the measurements into the README: both the pictures and the table.

The README is where a reader decides whether to trust the project, and the
fastest way to lose them is a number that no longer matches the run it claims
to come from. So neither the charts nor the table are written by hand. This
rebuilds both from `bench/results.json`, and `tests/test_charts.py` fails the
build if what is committed is not what this would produce.

    python3 bench/publish.py                    # rewrite charts and README
    python3 bench/publish.py --check            # say what is stale, change nothing

The table goes between the two markers below. Anything outside them is yours.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chart import charts                                         # noqa: E402
from compare import summarise                                    # noqa: E402

OPEN = "<!-- bench:table -->"
CLOSE = "<!-- /bench:table -->"


def block(payload: dict) -> str:
    """The head-to-head table, straight out of the results file."""
    rows = payload["runs"]
    totals = summarise(rows)
    tasks = len({r["task"] for r in rows})
    order = sorted(totals, key=lambda a: (-totals[a]["rate"],
                                          totals[a]["median_seconds"]))
    lines = ["| agent | solved | median task | whole benchmark | model calls per task |",
             "| --- | --- | --- | --- | --- |"]
    for agent in order:
        entry = totals[agent]
        seconds = sum(entry["seconds"])
        asked = sum(r.get("requests") or 0 for r in rows if r["agent"] == agent)
        drafts = sum(r.get("drafts") or 0 for r in rows if r["agent"] == agent)
        calls = "%.1f" % ((asked + drafts) / entry["runs"]) if (asked or drafts) else "—"
        lines.append("| %s | %d/%d (%d%%) | %.1fs | %.0fs | %s |"
                     % (agent, entry["passed"], entry["runs"], entry["rate"],
                        entry["median_seconds"], seconds, calls))
    lines.append("")
    # The note written when the results were merged says it better than a
    # generated sentence can; the generated one is only for a file without one.
    lines.append(payload.get("note")
                 or "%d tasks, one attempt each, measured %s."
                 % (tasks, payload.get("when", "").split()[0] or "—"))
    return "\n".join(lines)


def rewrite(readme: str, body: str) -> str:
    if OPEN not in readme or CLOSE not in readme:
        raise SystemExit("README has no %s / %s markers" % (OPEN, CLOSE))
    head, rest = readme.split(OPEN, 1)
    _, tail = rest.split(CLOSE, 1)
    return "%s%s\n\n%s\n\n%s%s" % (head, OPEN, body, CLOSE, tail)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(here, "results.json"))
    ap.add_argument("--readme", default=os.path.join(root, "README.md"))
    ap.add_argument("--out", default=os.path.join(root, "docs", "assets"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    with open(args.results, encoding="utf-8") as fh:
        payload = json.load(fh)
    with open(args.readme, encoding="utf-8") as fh:
        readme = fh.read()
    wanted = rewrite(readme, block(payload))

    if args.check:
        stale = []
        if wanted != readme:
            stale.append(args.readme)
        for path in charts(payload, os.path.join(args.out, ".check")):
            live = os.path.join(args.out, os.path.basename(path))
            if not os.path.exists(live) or open(live, "rb").read() != open(path, "rb").read():
                stale.append(live)
            os.remove(path)
        os.rmdir(os.path.join(args.out, ".check"))
        for path in stale:
            print("stale: %s" % path)
        print("everything matches bench/results.json" if not stale else
              "run bench/publish.py")
        return 1 if stale else 0

    for path in charts(payload, args.out):
        print("wrote %s" % path)
    with open(args.readme, "w", encoding="utf-8") as fh:
        fh.write(wanted)
    print("wrote %s" % args.readme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
