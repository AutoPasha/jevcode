"""Does the file-picking step actually find the right file?

Twenty questions about this repository, each with the file that answers it.
No embeddings are built and nothing is indexed: every run judges the whole tree
from scratch, which is the point being measured.

    TYPESAFE_API_KEY=... python3 bench/locate.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevcode import locate                       # noqa: E402
from jevcode.repo import Repo                    # noqa: E402
from jevcode.systemone import SystemOne, Usage   # noqa: E402

CASES = [
    ("where candidate patches that do not parse are thrown away", "jevcode/patch.py"),
    ("the retry and backoff policy for the API", "jevcode/systemone.py"),
    ("how a shell command is refused before it runs", "jevcode/gate.py"),
    ("the wording of every question the agent asks", "jevcode/questions.py"),
    ("turning a file into named, addressable slices", "jevcode/repo.py"),
    ("the loop that decides what to do next", "jevcode/engine.py"),
    ("asking a cheap model for several drafts at once", "jevcode/writer.py"),
    ("beam search over future actions", "jevcode/plan.py"),
    ("what gets printed while the agent works", "jevcode/trace.py"),
    ("parsing command line flags", "jevcode/cli.py"),
    ("undoing an edit after the tests fail", "jevcode/act.py"),
    ("pulling literal search strings out of the task", "jevcode/locate.py"),
    ("the probability above which the agent stops and asks a person", "jevcode/questions.py"),
    ("how the package is installed and what the entry point is", "pyproject.toml"),
    ("checks that run without touching the network", "tests/test_offline.py"),
]


def main() -> int:
    usage = Usage()
    one = SystemOne(usage=usage)
    repo = Repo(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    options = locate.shortlist(repo, " ".join(q for q, _ in CASES))
    hit = 0
    started = time.time()
    for question, expected in CASES:
        found = locate.pick_file(one, repo, question,
                                 options=locate.shortlist(repo, question))
        ok = found["file"] == expected
        hit += ok
        print("%s %-58s → %s (p=%.2f)"
              % ("ok  " if ok else "MISS", question[:58], found["file"],
                 found["ranked"][0][1]))
    print("\n%d/%d correct · %d files in the shortlist · %d questions in %d requests"
          % (hit, len(CASES), len(options), usage.questions, usage.requests))
    print("%.1fs, %.4f %s" % (time.time() - started, usage.cost, usage.currency))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
