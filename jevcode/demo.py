"""`jevcode --demo`: the whole interface, with nobody home.

Two stand-in models that answer from a table instead of over the network. It
exists so you can see what the thing feels like before you have a key, and so
the interface can be recorded and tested without spending anything.

It is not a simulation of quality. Nothing here decides anything: the numbers
are fixed, the code it writes is canned. Every measurement in the README comes
from real runs against real models — this is the showroom, not the road test.
"""

from __future__ import annotations

import random
import time

from .systemone import Answers, Usage
from .writer import Draft, WriterUsage

PAUSE = 0.35            # so the decisions are readable rather than instant


class DemoOne:
    """Plausible answers to any question set, with plausible timing."""

    def __init__(self, usage: Usage | None = None, pause: float = PAUSE):
        self.usage = usage or Usage()
        self.pause = pause
        self.steps = 0
        self.rng = random.Random(7)

    def ask(self, state, questions: dict) -> Answers:
        time.sleep(self.pause)
        self.usage.requests += 1
        self.usage.questions += len(questions)
        self.usage.input_tokens += 900 + 40 * len(questions)
        self.usage.seconds += self.pause
        if "action" in questions:
            self.steps += 1
        self.task = (state or {}).get("task", "") if isinstance(state, dict) else ""
        out = {}
        for name, q in questions.items():
            if q["type"] == "noul":
                out[name] = {"noul": self._noul(name)}
            elif q["type"] == "choice":
                out[name] = self._choice(name, list(q.get("criteria") or {}))
            else:
                out[name] = {"score": 2.6}
        return Answers(out)

    def _noul(self, name: str) -> float:
        if name == "done":
            return 0.93 if self.steps >= 3 else 0.08
        if name in ("needs_human", "regressed", "more_places", "destructive",
                    "off_task", "outside", "our_fault"):
            return round(self.rng.uniform(0.02, 0.12), 2)
        if name.startswith("worth_"):
            return round(self.rng.uniform(0.35, 0.95), 2)
        return round(self.rng.uniform(0.6, 0.95), 2)

    def _choice(self, name: str, keys: list) -> dict:
        if not keys:
            return {"choice": "", "probabilities": {}, "confidence": 0.0}
        if name == "action":
            order = ["read", "edit", "run", "finish"]
            pick = order[min(self.steps - 1, len(order) - 1)]
            pick = pick if pick in keys else keys[0]
        elif name == "file":
            pick = _likeliest(keys, self.task)
        elif name == "region":
            pick = next((k for k in keys if "__init__" in k), keys[0])
        elif name == "best":
            pick = keys[0]
        else:
            pick = keys[0]
        rest = [k for k in keys if k != pick]
        share = (1 - 0.68) / max(len(rest), 1)
        probs = {pick: 0.68}
        probs.update({k: round(share, 4) for k in rest})
        return {"choice": pick, "probabilities": probs, "confidence": 0.81}


class DemoWriter:
    """Returns the region it was given, with one plausible line added."""

    def __init__(self, usage: WriterUsage | None = None, pause: float = 0.6):
        self.usage = usage or WriterUsage()
        self.pause = pause

    def drafts(self, prompt: str, n: int = 4, system: str = "", max_tokens: int = 1600,
               spread: float = 0.25, enough: int = 0, grace: float = 2.5) -> list:
        time.sleep(self.pause)
        self.usage.calls += n
        self.usage.input_tokens += 1200 * n
        self.usage.output_tokens += 180 * n
        self.usage.seconds += self.pause
        out = []
        for i, code in enumerate(_canned_drafts(_region_of(prompt), n)):
            out.append(Draft(i, "```\n%s\n```" % code, code, round(self.pause, 2)))
        return out


def _likeliest(keys: list, task: str) -> str:
    """The file a person would open first: named in the task, and not its test."""
    words = [w.lower() for w in task.replace("/", " ").split() if len(w) > 3]
    scored = []
    for key in keys:
        low = key.lower()
        score = sum(1 for w in words if w in low)
        if "test" in low or "spec" in low:
            score -= 2
        scored.append((score, -len(key), key))
    scored.sort(reverse=True)
    return scored[0][2]


def _canned_drafts(region: str, n: int) -> list:
    """What a writer asked six times actually returns: a spread, not one answer.

    Two of these are right, one is right in a clumsier way, and the rest repeat
    the code unchanged — which is exactly the pile the judge exists to sort,
    and what makes the demo show the dedupe and the ranking rather than a
    single suspiciously perfect draft.
    """
    fixed = region.replace("start=1", "start=0").replace("self.value = 1",
                                                         "self.value = 0")
    indent = region[:len(region) - len(region.lstrip(" "))]
    wordy = fixed + ("\n%s    # counting starts at zero" % indent
                     if fixed.rstrip().endswith(("start", "0")) else "")
    pile = [fixed, region, wordy, fixed, region, region, fixed, region]
    return pile[:max(n, 1)]


def _region_of(prompt: str) -> str:
    """Pull the region back out of the brief the agent assembled.

    The brief puts three optional blocks after the region — related code, the
    previous failure, the closing instruction — so the cut has to be at
    whichever comes first, and only newlines may be trimmed: stripping spaces
    would dedent the first line and produce a patch that cannot parse.
    """
    marker = "The exact lines you are replacing:"
    if marker not in prompt:
        return "pass"
    tail = prompt.split(marker, 1)[1]
    for after in ("\nOther code that has to keep working",
                  "\nA previous attempt at this region",
                  "\nWrite the replacement for lines"):
        if after in tail:
            tail = tail.split(after, 1)[0]
    return tail.strip("\n")


# ------------------------------------------------------------- a safe playground

SAMPLE = {
    "counter.py": '''"""A counter with an off-by-one nobody has fixed yet."""


class Counter:
    def __init__(self, start=1):
        self.value = start

    def bump(self, by=1):
        self.value += by
        return self.value

    def reset(self):
        self.value = 1
''',
    "tests/test_counter.py": '''import unittest

from counter import Counter


class CounterTest(unittest.TestCase):
    def test_starts_at_zero(self):
        self.assertEqual(Counter().value, 0)

    def test_bumps(self):
        c = Counter()
        c.bump()
        self.assertEqual(c.value, 1)


if __name__ == "__main__":
    unittest.main()
''',
    "Makefile": "test:\n\tpython3 -m unittest discover -s tests -q\n",
    "AGENTS.md": ("# counter\n\nA one-file sample project, here so the demo has "
                  "something real to change.\n\n## Commands\n\n- `make test`\n"),
}


def playground() -> str:
    """A throwaway project for `--demo` to work in.

    A tour that edits the repository you happen to be standing in is not a
    tour, it is an accident. So the demo gets its own directory with a real
    bug in it, and `/diff`, `/undo` and the tests all mean something there.
    """
    import os
    import tempfile
    root = tempfile.mkdtemp(prefix="jevcode-demo-")
    for rel, body in SAMPLE.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return root
