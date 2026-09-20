"""Stand-ins for the two models, so the whole agent can be run without a key.

They are not mocks in the usual sense — nothing here asserts on calls. They
answer the real question shapes with plausible numbers, which is what makes it
possible to test the loop, the terminal and the session store end to end and
still have the test finish in milliseconds.
"""

from __future__ import annotations

from jevcode.systemone import Answers, Usage
from jevcode.writer import Draft, WriterUsage


class FakeOne:
    """Answers any question set. `script` forces particular actions in order."""

    def __init__(self, script=None, done_after: int = 2, usage=None):
        self.script = list(script or [])
        self.done_after = done_after
        self.asked: list = []
        self.steps = 0
        self.usage = usage or Usage()

    def ask(self, state, questions: dict) -> Answers:
        self.asked.append(questions)
        self.usage.requests += 1
        self.usage.questions += len(questions)
        out = {}
        wanted = self.script.pop(0) if self.script else None
        if "action" in questions:
            self.steps += 1
        for name, q in questions.items():
            kind = q["type"]
            options = q.get("criteria")
            if kind == "noul":
                out[name] = {"noul": self._noul(name)}
            elif kind == "choice":
                keys = list(options)
                pick = wanted if (name == "action" and wanted in keys) else keys[0]
                probs = {k: (0.7 if k == pick else 0.3 / max(len(keys) - 1, 1))
                         for k in keys}
                out[name] = {"choice": pick, "probabilities": probs, "confidence": 0.8}
            else:
                out[name] = {"score": 3.0}
        return Answers(out)

    def _noul(self, name: str) -> float:
        if name == "done":
            return 0.95 if self.steps > self.done_after else 0.1
        if name in ("needs_human", "regressed", "more_places", "destructive",
                    "off_task", "outside", "our_fault"):
            return 0.05
        return 0.9


class FakeWriter:
    """Returns the same replacement every time, wrapped in a fence."""

    def __init__(self, code: str = "value = 2\n", usage=None):
        self.code = code
        self.calls = 0
        self.usage = usage or WriterUsage()

    def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
               enough=0, grace=2.5):
        self.calls += 1
        self.usage.calls += n
        return [Draft(i, "```\n%s```" % self.code, self.code, 0.01)
                for i in range(n)]
