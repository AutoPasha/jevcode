"""The tests decide which draft wins, and they decide it all at once.

What these cover is the difference between an agent that spends six suites and
a request finding out which of six candidates is right, and one that spends a
single suite's worth of waiting and knows. The second one is also right more
often: a ranking can prefer a draft that does not work, and a test run cannot.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from jevcode import tryout, verify
from jevcode.engine import Agent
from jevcode.repo import Repo
from jevcode.trace import Trace
from jevcode.writer import Draft, WriterUsage

from fakes import FakeOne


SUITE = ("import unittest\nfrom work import answer\n\n"
         "class T(unittest.TestCase):\n"
         "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n")


class Spread:
    """A writer that returns several different drafts, only one of which works."""

    def __init__(self, bodies: list):
        self.bodies = bodies
        self.calls = 0
        self.usage = WriterUsage()

    def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
               enough=0, grace=2.5):
        self.calls += 1
        self.usage.calls += len(self.bodies)
        return [Draft(i, "```\n%s```" % body, body, 0.01)
                for i, body in enumerate(self.bodies)]


class Ranked(FakeOne):
    """FakeOne that puts a named candidate at the top of the pile."""

    def __init__(self, favourite: str, **kw):
        super().__init__(**kw)
        self.favourite = favourite
        self.judged = 0

    def ask(self, state, questions):
        answers = super().ask(state, questions)
        if "best" in questions:
            self.judged += 1
            keys = list(questions["best"].get("criteria") or {})
            probs = {k: (0.9 if k == self.favourite else 0.1 / max(len(keys) - 1, 1))
                     for k in keys}
            answers["best"] = {"choice": self.favourite, "probabilities": probs,
                               "confidence": 0.9}
        return answers


class Repository(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevrace-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.put("work.py", "def answer():\n    pass\n")
        self.put("test_work.py", SUITE)
        self.put("Makefile", "test:\n\tpython3 -m unittest discover -q\n")

    def put(self, name, body):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write(body)


class Racing(Repository):
    def test_every_candidate_is_run_and_the_repo_is_left_alone(self):
        repo = Repo(self.root)
        candidates = [_cand("A", "def answer():\n    return 0\n"),
                      _cand("B", "def answer():\n    return 42\n")]
        trials = tryout.race(repo, "make test", "work.py", 1, 2, candidates)
        self.assertEqual(set(trials), {"A", "B"})
        self.assertFalse(trials["A"].ok)
        self.assertTrue(trials["B"].ok)
        self.assertIn("pass", repo.read("work.py"), "the real file was touched")

    def test_green_wins_over_the_ranking(self):
        trials = {"A": _trial("A", 0, "2 failed, 0 passed in 0.1s\n"),
                  "B": _trial("B", 0, "2 passed in 0.1s\n", code=0)}
        before = verify.parse("2 failed, 0 passed in 0.1s\n", 1)
        self.assertEqual(tryout.pick(trials, before, order=["A", "B"]), "B")

    def test_progress_wins_when_nothing_is_green(self):
        trials = {"A": _trial("A", 1, "2 failed, 0 passed in 0.1s\n"),
                  "B": _trial("B", 1, "1 failed, 1 passed in 0.1s\n")}
        before = verify.parse("2 failed, 0 passed in 0.1s\n", 1)
        self.assertEqual(tryout.pick(trials, before), "B")

    def test_nothing_wins_when_nothing_moves(self):
        trials = {"A": _trial("A", 1, "2 failed, 0 passed in 0.1s\n"),
                  "B": _trial("B", 1, "2 failed, 0 passed in 0.1s\n")}
        before = verify.parse("2 failed, 0 passed in 0.1s\n", 1)
        self.assertEqual(tryout.pick(trials, before), "")

    def test_a_request_is_only_worth_it_when_the_tests_cannot_choose(self):
        before = verify.parse("2 failed, 0 passed in 0.1s\n", 1)
        same = {"A": _trial("A", 1, "1 failed, 1 passed in 0.1s\n"),
                "B": _trial("B", 1, "1 failed, 1 passed in 0.1s\n")}
        self.assertTrue(tryout.undecided(same, before))
        green = {"A": _trial("A", 0, "2 passed in 0.1s\n", code=0),
                 "B": _trial("B", 0, "2 passed in 0.1s\n", code=0)}
        self.assertFalse(tryout.undecided(green, before),
                         "two right answers are not a question")


class InTheLoop(Repository):
    def agent(self, bodies, favourite="A"):
        one = Ranked(favourite, script=["edit"], done_after=99)
        writer = Spread(bodies)
        return Agent("make answer() return 42", self.root, one=one, writer=writer,
                     trace=Trace(quiet=True), candidates=len(bodies), max_steps=4), one

    def test_the_working_draft_wins_even_when_it_is_ranked_last(self):
        agent, one = self.agent(["def answer():\n    return 0\n",
                                 "def answer():\n    return 1\n",
                                 "def answer():\n    return 42\n"])
        outcome = agent.step(1)
        self.assertIn("return 42", open(os.path.join(self.root, "work.py")).read())
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.finished)
        self.assertTrue(agent.verified)
        self.assertEqual(one.judged, 0, "the tests answered; no request was needed")

    def test_the_suite_is_not_run_twice_for_the_winner(self):
        agent, _ = self.agent(["def answer():\n    return 0\n",
                               "def answer():\n    return 42\n"])
        agent.step(1)
        # One baseline before anything was written, and one check recorded for
        # the candidate that was applied. The race itself runs in copies.
        self.assertEqual(len(agent.checks), 1)
        self.assertTrue(agent.checks[0]["ok"])

    def test_a_round_that_moves_nothing_counts_as_stuck(self):
        agent, _ = self.agent(["def answer():\n    return 0\n",
                               "def answer():\n    return 1\n"])
        agent.step(1)
        self.assertEqual(agent.edits, [])
        self.assertEqual(agent.stuck, 1)
        self.assertIn("pass", open(os.path.join(self.root, "work.py")).read())


class Blanks(unittest.TestCase):
    """A class with nothing in its methods is a class to write, not to patch."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevblank-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name, body):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write(body)

    def test_a_hollow_class_is_a_blank(self):
        self.write("robot.py", "class Robot:\n    def __init__(self):\n        pass\n")
        repo = Repo(self.root)
        self.assertIn("Robot", [s.name for s in repo.stubs("robot.py")])
        self.assertTrue(repo.greenfield("robot.py"))

    def test_a_class_with_one_written_method_is_not(self):
        self.write("half.py", "class Half:\n    def a(self):\n        return 1\n\n"
                              "    def b(self):\n        pass\n")
        repo = Repo(self.root)
        self.assertNotIn("Half", [s.name for s in repo.stubs("half.py")])

    def test_one_blank_among_written_code_does_not_rewrite_the_file(self):
        self.write("mixed.py",
                   "class Done:\n    def a(self):\n        return 1\n\n\n"
                   "class Todo:\n    def b(self):\n        pass\n")
        repo = Repo(self.root)
        self.assertFalse(repo.greenfield("mixed.py"),
                         "the finished class must not be rewritten for the empty one")

    def test_the_blank_region_is_authored_in_full(self):
        self.write("mixed.py",
                   "class Done:\n    def a(self):\n        return 1\n\n\n"
                   "class Todo:\n    def b(self):\n        pass\n")
        agent = Agent("write Todo", self.root, one=FakeOne(), writer=Spread(["x"]),
                      trace=Trace(quiet=True))
        region = next(r for r in agent.repo.regions("mixed.py") if r.name == "Todo")
        scoped, whole = agent._scoped("mixed.py", region)
        self.assertTrue(whole, "a blank class has to be written, not patched")
        self.assertEqual((scoped.start, scoped.end), (region.start, region.end))


def _cand(letter, code):
    from jevcode.patch import Candidate
    return Candidate(letter, code)


def _trial(letter, code_in, output, code=None):
    from jevcode import act
    run = act.Run("make test", code if code is not None else code_in, output, 0.1)
    return tryout.Trial(letter, run, verify.parse(output, run.code))


if __name__ == "__main__":
    unittest.main()
