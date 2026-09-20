"""What the kept-alive connection is actually worth.

An agent that asks a lot of small questions pays for the handshake more often
than for the thinking, and that cost hides inside "the model is slow". This
asks the same trivial question N times, with the pool and without it, and
prints both medians.

    python3 bench/handshake.py --times 10
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def measure(times: int, keepalive: bool) -> list:
    os.environ["JEVCODE_NO_KEEPALIVE"] = "0" if keepalive else "1"
    for name in [m for m in list(sys.modules) if m.startswith("jevcode")]:
        del sys.modules[name]
    from jevcode.systemone import SystemOne, noul

    one = SystemOne()
    state = {"note": "a warm-up question, nothing depends on the answer"}
    seconds = []
    for _ in range(times):
        started = time.time()
        one.ask(state, {"q": noul("Is this a question?")})
        seconds.append(time.time() - started)
    return seconds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--times", type=int, default=10)
    args = ap.parse_args()

    for keepalive in (False, True):
        seconds = measure(args.times, keepalive)
        print("%-12s median %.3fs  first %.3fs  rest %.3fs" % (
            "kept alive" if keepalive else "fresh socket",
            statistics.median(seconds), seconds[0],
            statistics.median(seconds[1:]) if len(seconds) > 1 else seconds[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
