"""The loop has to stop when the project itself says the work is done.

Both of these describe the same rule from opposite sides. A run of the
project's own check passing after an edit is a fact about the world; `done` and
`more_places` are opinions about it. When the fact is in, the opinions do not
get to keep the agent going — that is what burned 14 steps and 72 drafts on a
task whose tests were already green.

These are offline: the fake writer produces the fix, the fake Jev is told to be
as unhelpful as the real one was.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevcode.engine import Agent                       # noqa: E402
from jevcode.systemone import Answers, Usage           # noqa: E402
from jevcode.trace import Trace                        # noqa: E402
from jevcode.writer import Draft, WriterUsage          # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import Streams                             # noqa: E402

CHECK = """test:
\t@grep -q 'value = 2' a.py
"""


class StubbornOne:
    """Jev at its least helpful: never says done, always suspects more work.

    `more_places` above the threshold is the shape that actually happened on
    ttlcache — it drags `finish` down to a tenth of its weight, so the agent
    cannot choose to stop even once the tests are green.
    """

    def __init__(self, script=None, file="a.py"):
        self.script = list(script or [])
        self.file = file
        self.usage = Usage()
        self.steps = 0

    def ask(self, state, questions: dict) -> Answers:
        self.usage.requests += 1
        self.usage.questions += len(questions)
        wanted = self.script.pop(0) if self.script else None
        if "action" in questions:
            self.steps += 1
        out = {}
        for name, q in questions.items():
            if q["type"] == "noul":
                out[name] = {"noul": self._noul(name)}
            elif q["type"] == "choice":
                keys = list(q.get("criteria") or {})
                pick = keys[0]
                if name == "action" and wanted in keys:
                    pick = wanted
                elif name == "file" and self.file in keys:
                    # Without this the stand-in picks alphabetically and
                    # "edits" the Makefile, which tests the fake, not the loop.
                    pick = self.file
                out[name] = {"choice": pick,
                             "probabilities": {k: (0.7 if k == pick else
                                                   0.3 / max(len(keys) - 1, 1))
                                               for k in keys},
                             "confidence": 0.8}
            else:
                out[name] = {"score": 3.0}
        return Answers(out)

    @staticmethod
    def _noul(name: str) -> float:
        if name == "done":
            return 0.10          # never admits the task is finished
        if name == "more_places":
            return 0.70          # always suspects another file needs changing
        if name in ("needs_human", "regressed", "destructive", "off_task",
                    "outside", "our_fault"):
            return 0.05
        return 0.9


class FixingWriter(Streams):
    """Writes the one change that turns the project's check green."""

    def __init__(self):
        self.usage = WriterUsage()
        self.calls = 0

    def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
               enough=0, grace=2.5):
        self.calls += 1
        self.usage.calls += n
        code = "value = 2\n"
        return [Draft(i, "```\n%s```" % code, code, 0.01) for i in range(n)]


class GreenMeansStop(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevstop-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.put("a.py", "value = 1\n")
        self.put("Makefile", CHECK)

    def put(self, rel: str, body: str) -> None:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def run_agent(self, **kw):
        one, writer = StubbornOne(script=["read", "edit"]), FixingWriter()
        agent = Agent("make value two", self.root, one=one, writer=writer,
                      trace=Trace(quiet=True), candidates=2, **kw)
        return agent, one, writer, agent.run()

    def test_a_green_check_ends_the_run(self):
        agent, one, writer, outcome = self.run_agent()
        self.assertTrue(outcome.finished, outcome.reason)
        self.assertLessEqual(outcome.steps, 4,
                             "kept going after the project's own check passed")

    def test_it_does_not_keep_rewriting_a_file_that_already_passes(self):
        agent, one, writer, outcome = self.run_agent()
        self.assertLessEqual(writer.calls, 2,
                             "wrote the same region again after it was green")

    def test_without_a_check_command_the_opinion_still_decides(self):
        """No command to run means no fact to go on: the old path stays."""
        os.remove(os.path.join(self.root, "Makefile"))
        agent, one, writer, outcome = self.run_agent(allow_commands=False,
                                                     max_steps=6)
        self.assertFalse(outcome.finished)


if __name__ == "__main__":
    unittest.main()
