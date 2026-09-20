"""Собрать один results.json из отдельных прогонов bench/.runs/.

Каждый прогон делается своим вызовом compare.py — одна пара «участник плюс
задача» за раз, — потому что долгая очередь рвётся по таймауту где-нибудь на
середине, и тогда теряется всё. Отдельные файлы не теряются: упал седьмой
прогон, шесть предыдущих остались на диске.

    python3 bench/merge.py --runs bench/.runs --out bench/results.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import table  # noqa: E402


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=os.path.join(here, ".runs"))
    ap.add_argument("--out", default=os.path.join(here, "results.json"))
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    rows: list = []
    for path in sorted(glob.glob(os.path.join(args.runs, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.load(fh).get("runs") or [])
    if not rows:
        print("no runs in %s" % args.runs, file=sys.stderr)
        return 2

    payload = {
        "when": time.strftime("%Y-%m-%d %H:%M"),
        "note": args.note,
        "tasks": sorted({r["task"] for r in rows}),
        "agents": sorted({r["agent"] for r in rows}),
        "runs": rows,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("wrote %s (%d runs)" % (args.out, len(rows)))
    print()
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
