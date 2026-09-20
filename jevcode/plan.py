"""Thinking three moves ahead, in one request.

An ordinary agent cannot look ahead. To find out what comes after a step it has
to take the step: run the model, run the tool, read the result, run the model
again. Every branch it considers costs a full round trip, so in practice it
considers one — the first plausible move — and discovers the rest by walking
into it.

System One changes the arithmetic. Questions are independent and a request can
carry hundreds of them, so a whole tree of hypothetical futures fits in a single
call. The trick is that each branch carries its own assumed history inside the
state, and each question points at its own branch by path — `branches.b2` — so
one request answers "what next" for every branch at once.

The result is a beam search over actions: width three, depth three, one request,
about a second. Scores are the geometric mean of the probabilities along a path,
the same way the hierarchical-classification cookbook combines levels, so a long
path is not punished merely for being long.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .questions import ACTIONS
from .systemone import choice


@dataclass
class Branch:
    steps: list = field(default_factory=list)
    probability: float = 1.0
    decisions: int = 0

    @property
    def score(self) -> float:
        if not self.decisions:
            return self.probability
        return self.probability ** (1.0 / self.decisions)

    def extended(self, action: str, p: float) -> "Branch":
        return Branch(self.steps + [action], self.probability * max(p, 1e-9),
                      self.decisions + 1)

    def __str__(self) -> str:
        return " → ".join(self.steps)


def lookahead(one, state: dict, depth: int = 3, beam: int = 3,
              actions: dict | None = None) -> list:
    """The `beam` most likely action sequences, `depth` steps out.

    One request per level, not per branch: the branches live in the state and
    the questions address them by name.
    """
    actions = actions or ACTIONS
    beams = [Branch()]
    for _ in range(depth):
        live = [b for b in beams if not b.steps or b.steps[-1] != "finish"]
        if not live:
            break
        branches = {"b%d" % i: (b.steps or ["(nothing yet)"]) for i, b in enumerate(live)}
        questions = {
            "next_b%d" % i: choice({
                "question": "Assuming the steps in `branches.b%d` have already been carried "
                            "out and each returned what it usually returns, what should the "
                            "agent do next to carry out `task`?" % i,
            }, {name: spec for name, spec in actions.items()})
            for i in range(len(live))
        }
        answers = one.ask({**state, "branches": branches}, questions)
        grown = []
        for i, branch in enumerate(live):
            for name, p in answers.ranked("next_b%d" % i)[:beam]:
                grown.append(branch.extended(name, p))
        grown += [b for b in beams if b.steps and b.steps[-1] == "finish"]
        grown.sort(key=lambda b: -b.score)
        beams = grown[:beam]
    return beams
