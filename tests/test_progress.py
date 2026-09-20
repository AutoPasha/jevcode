"""The agent is allowed to keep work that is not finished yet.

The behaviour these cover is the difference between an agent that solves a task
in three steps and one that rewrites the same region until its budget runs out:
a change that leaves the suite red but makes more of it pass is progress, and
progress is kept. The rest is what that rests on — reading a test runner's own
counts, and noticing before writing anything that a file is a skeleton rather
than code to patch.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from jevcode import verify
from jevcode.engine import Agent
from jevcode.repo import Repo
from jevcode.trace import Trace
from jevcode.writer import Draft, WriterUsage

from fakes import FakeOne


PYTEST_RED = "....FF                       [100%]\n2 failed, 4 passed in 0.12s\n"
PYTEST_GREEN = "......                       [100%]\n6 passed in 0.10s\n"
UNITTEST_RED = "FF..\nRan 4 tests in 0.001s\n\nFAILED (failures=2)\n"
UNITTEST_GREEN = "....\nRan 4 tests in 0.001s\n\nOK\n"


class Reading(unittest.TestCase):
    def test_pytest_counts(self):
        red = verify.parse(PYTEST_RED, 1)
        self.assertTrue(red.readable)
        self.assertEqual((red.passed, red.failed), (4, 2))
        green = verify.parse(PYTEST_GREEN, 0)
        self.assertEqual((green.passed, green.failed), (6, 0))
        self.assertTrue(green.ok)

    def test_unittest_counts(self):
        red = verify.parse(UNITTEST_RED, 1)
        self.assertEqual((red.passed, red.failed, red.total), (2, 2, 4))
        self.assertEqual(verify.parse(UNITTEST_GREEN, 0).passed, 4)

    def test_other_runners(self):
        go = verify.parse("--- PASS: TestA\n--- FAIL: TestB\nFAIL\n", 1)
        self.assertEqual((go.passed, go.failed), (1, 1))
        cargo = verify.parse("test result: FAILED. 3 passed; 2 failed; 0 ignored\n", 1)
        self.assertEqual((cargo.passed, cargo.failed), (3, 2))
        jest = verify.parse("Tests:       2 failed, 3 passed, 5 total\n", 1)
        self.assertEqual((jest.passed, jest.failed, jest.total), (3, 2, 5))

    def test_node_test_speaks_tap(self):
        """The JavaScript task in bench/tasks runs `node --test`, which is TAP.

        Left unparsed it read as "nothing changed" after every edit, and the
        loop gave up on a task it used to solve.
        """
        tap = verify.parse(
            "not ok 2 - takes a key function\n"
            "1..2\n# tests 2\n# suites 0\n# pass 1\n# fail 1\n"
            "# cancelled 0\n# skipped 0\n# todo 0\n# duration_ms 165\n", 1)
        self.assertTrue(tap.readable)
        self.assertEqual((tap.passed, tap.failed, tap.total), (1, 1, 2))
        self.assertEqual(tap.failing, {"takes a key function"})

    def test_a_missing_runner_is_not_a_verdict_on_the_change(self):
        gone = verify.parse("/bin/sh: 1: pytest: not found\n", 127)
        self.assertFalse(gone.readable)
        self.assertTrue(gone.missing)
        self.assertFalse(verify.parse(PYTEST_RED, 1).missing)


class Progress(unittest.TestCase):
    def test_more_passing_is_progress_even_while_red(self):
        before = verify.parse("6 failed, 0 passed in 0.1s\n", 1)
        after = verify.parse("4 failed, 2 passed in 0.1s\n", 1)
        self.assertTrue(verify.better(after, before))
        self.assertFalse(verify.better(before, after))

    def test_a_smaller_suite_is_not_progress(self):
        # A patch that breaks collection reports one error where there were
        # nine failures, and must not be mistaken for an improvement.
        before = verify.parse("9 failed, 0 passed in 0.1s\n", 1)
        after = verify.parse("1 error in 0.1s\n", 1)
        self.assertFalse(verify.better(after, before))

    def test_unreadable_output_decides_nothing(self):
        before = verify.parse(PYTEST_RED, 1)
        self.assertFalse(verify.better(verify.parse("boom\n", 1), before))


class Skeletons(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevstub-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name: str, body: str) -> None:
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write(body)

    def test_two_empty_bodies_make_a_file_to_write_whole(self):
        self.write("cipher.py", "def encode(text):\n    pass\n\n\n"
                                "def decode(text):\n    pass\n")
        repo = Repo(self.root)
        self.assertEqual(len(repo.stubs("cipher.py")), 2)
        self.assertTrue(repo.greenfield("cipher.py"))

    def test_written_code_is_not_a_skeleton(self):
        self.write("real.py", 'def one():\n    return 1\n\n\ndef two():\n    return 2\n')
        self.assertFalse(Repo(self.root).greenfield("real.py"))

    def test_a_docstring_over_nothing_is_still_a_skeleton(self):
        self.write("doc.py", 'def one():\n    """What it will do."""\n\n\n'
                             'def two():\n    raise NotImplementedError("later")\n')
        self.assertTrue(Repo(self.root).greenfield("doc.py"))


class StagedWriter:
    """A writer whose reply depends on which file it was asked about."""

    def __init__(self, by_path: dict):
        self.by_path = by_path
        self.calls = 0
        self.usage = WriterUsage()

    def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
               enough=0, grace=2.5):
        self.calls += 1
        self.usage.calls += n
        code = next((body for path, body in self.by_path.items() if path in prompt), "pass\n")
        return [Draft(i, "```\n%s```" % code, code, 0.01) for i in range(n)]


class Picky(FakeOne):
    """FakeOne, plus a fixed answer for named choices — so a two-file task can
    be driven through both of its files in a known order."""

    def __init__(self, picks: dict, **kw):
        super().__init__(**kw)
        self.picks = picks

    def ask(self, state, questions):
        answers = super().ask(state, questions)
        for name, wanted in self.picks.items():
            if name not in answers:
                continue
            keys = list(questions[name].get("criteria") or {})
            pick = next((k for k in keys if wanted in k), None)
            if pick:
                probs = {k: (0.8 if k == pick else 0.2 / max(len(keys) - 1, 1))
                         for k in keys}
                answers[name] = {"choice": pick, "probabilities": probs,
                                 "confidence": 0.9}
        return answers


class HalfAChange(unittest.TestCase):
    """The case the agent used to lose: a task that needs two files.

    Either half alone leaves the suite red. Under the old rule both halves were
    correct, both were undone, and the agent started again from an empty file
    every time until the step budget was gone.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevhalf-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.put("alpha.py", "def add(a, b):\n    pass\n")
        self.put("beta.py", "def mul(a, b):\n    pass\n")
        # unittest, not pytest: pytest is not installed on every machine that
        # runs this suite, and a test about reading a test run must not depend
        # on a runner that might be missing.
        self.put("test_suite.py",
                 "import unittest\nfrom alpha import add\nfrom beta import mul\n\n"
                 "class Both(unittest.TestCase):\n"
                 "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n"
                 "    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n")
        self.put("Makefile", "test:\n\tpython3 -m unittest discover -q\n")

    def put(self, name, body):
        with open(os.path.join(self.root, name), "w", encoding="utf-8") as fh:
            fh.write(body)

    def agent(self, picks, script):
        writer = StagedWriter({"alpha.py": "def add(a, b):\n    return a + b\n",
                               "beta.py": "def mul(a, b):\n    return a * b\n"})
        one = Picky(picks, script=script, done_after=99)
        return Agent("make add and mul work", self.root, one=one, writer=writer,
                     trace=Trace(quiet=True), candidates=2, max_steps=8), writer

    def test_the_first_half_survives_a_red_suite(self):
        agent, _ = self.agent({"file": "alpha.py", "command": "make test"},
                              ["read", "edit"])
        agent.step(1)
        agent.step(2)
        self.assertEqual(len(agent.edits), 1, "the working half was thrown away")
        self.assertIn("return a + b", open(os.path.join(self.root, "alpha.py")).read())
        self.assertEqual(agent.stuck, 0)

    def test_both_halves_finish_the_task(self):
        agent, writer = self.agent({"command": "make test"},
                                   ["read", "edit", "read", "edit"])
        agent.one.picks["file"] = "alpha.py"
        agent.step(1)
        agent.step(2)
        agent.one.picks["file"] = "beta.py"
        agent.step(3)
        outcome = agent.step(4)
        self.assertIsNotNone(outcome, "a green suite after a red one ends the task")
        self.assertTrue(outcome.finished)
        self.assertTrue(agent.verified)
        # Two regions, one fan-out of drafts each. The old loop needed a fresh
        # fan-out for every retry of a half it had just discarded.
        self.assertEqual(writer.calls, 2)


if __name__ == "__main__":
    unittest.main()
