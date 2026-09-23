"""Drafts and test runs overlap, and the first green run ends the step.

The step used to be two waits stacked on top of each other: every draft had to
be written before the first test could start, and every test had to finish
before the winner was known. Neither wait earns anything. A draft that lands
last is not better for being slow, and a suite still running after another one
came back green is answering a question that is already settled.

What these cover is that both of those are now gone — and that nothing about
the answer changed: the tests still pick the candidate that works, and when
none of them works the runs that did finish are still there to choose from.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jevcode import act, tryout, verify                 # noqa: E402
from jevcode.engine import Agent                        # noqa: E402
from jevcode.patch import Candidate                     # noqa: E402
from jevcode.repo import Repo                           # noqa: E402
from jevcode.trace import Trace                         # noqa: E402
from jevcode.writer import Draft, WriterUsage           # noqa: E402

from fakes import FakeOne                               # noqa: E402

# Green the moment the answer is right, and five seconds of nothing for the
# wrong answer in particular — the stub itself fails fast, so the baseline run
# costs nothing and only a losing candidate drags. That is the whole point: if
# the step waited for a run whose verdict cannot matter, every test below would
# take five seconds longer.
SLOW_CHECK = """test:
\t@grep -q 'return 0' work.py && sleep 5 || true
\t@grep -q 'return 42' work.py
"""

WRONG = "def answer():\n    return 0\n"
RIGHT = "def answer():\n    return 42\n"
STUB = "def answer():\n    pass\n"


class Slowly:
    """A writer that hands over one draft at once and the rest after a wait."""

    def __init__(self, bodies: list, delay: float = 3.0):
        self.bodies = bodies
        self.delay = delay
        self.calls = 0
        self.usage = WriterUsage()
        self.abandoned = 0

    def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
               enough=0, grace=2.5):
        self.calls += 1
        time.sleep(self.delay)
        return [Draft(i, "```\n%s```" % body, body, 0.01)
                for i, body in enumerate(self.bodies)]

    def stream(self, prompt, n=4, system="", max_tokens=1600, spread=0.25, stop=None,
               enough=0, grace=2.5):
        self.calls += 1
        for i, body in enumerate(self.bodies):
            if i:
                # Everything after the first one is the straggler.
                waited = 0.0
                while waited < self.delay:
                    if stop is not None and stop.is_set():
                        self.abandoned += 1
                        return
                    time.sleep(0.05)
                    waited += 0.05
            yield Draft(i, "```\n%s```" % body, body, 0.01)


class Tree(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevpipe-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.repo = Repo(self.root)
        self.repo.write("work.py", STUB)
        self.repo.write("Makefile", SLOW_CHECK)

    def on_disk(self, name: str) -> str:
        """Straight off the disk: `Repo` caches, and the agent has its own copy."""
        with open(os.path.join(self.root, name), encoding="utf-8") as fh:
            return fh.read()


class Overlap(Tree):
    def test_the_first_green_run_ends_it_without_waiting_for_the_rest(self):
        """One draft is right and arrives first; three more are still being written."""
        writer = Slowly([RIGHT, WRONG, WRONG, WRONG], delay=3.0)
        started = time.time()
        source = (Candidate(letter, draft.code)
                  for letter, draft in zip("ABCD",
                                           writer.stream("", n=4, stop=None)))
        trials, winner, seen = tryout.pipeline(
            self.repo, "make test", "work.py", 1, 2, source, timeout=30)
        spent = time.time() - started
        self.assertEqual(winner, "A")
        self.assertEqual(len(seen), 1, "nobody should have waited for the stragglers")
        self.assertLess(spent, 3.0,
                        "the step waited for drafts whose verdict cannot matter")

    def test_a_losing_candidate_does_not_hold_the_step_to_its_own_timeout(self):
        """The wrong draft's run sleeps five seconds; the right one is instant."""
        source = iter([Candidate("A", WRONG), Candidate("B", RIGHT)])
        started = time.time()
        trials, winner, _ = tryout.pipeline(
            self.repo, "make test", "work.py", 1, 2, source, timeout=30)
        spent = time.time() - started
        self.assertEqual(winner, "B")
        self.assertLess(spent, 4.0, "the abandoned run was waited out")
        self.assertNotIn("A", trials, "an abandoned run is not a verdict")

    def test_nothing_green_keeps_every_verdict_for_the_caller(self):
        """No winner means the runs that finished are exactly what `pick` needs."""
        self.repo.write("Makefile", "test:\n\t@grep -q 'return 42' work.py\n")
        source = iter([Candidate("A", WRONG), Candidate("B", "def answer():\n    return 1\n")])
        trials, winner, seen = tryout.pipeline(
            self.repo, "make test", "work.py", 1, 2, source, timeout=30)
        self.assertEqual(winner, "")
        self.assertEqual(sorted(trials), ["A", "B"])
        self.assertEqual(len(seen), 2)

    def test_the_repository_itself_is_never_touched(self):
        source = iter([Candidate("A", RIGHT)])
        tryout.pipeline(self.repo, "make test", "work.py", 1, 2, source, timeout=30)
        self.assertEqual(self.on_disk("work.py"), STUB)


class InTheLoop(Tree):
    def agent(self, writer, candidates):
        return Agent("make answer() return 42", self.root,
                     one=FakeOne(script=["edit"], done_after=99), writer=writer,
                     trace=Trace(quiet=True), candidates=candidates, max_steps=4)

    def test_the_agent_finishes_on_the_first_draft_while_the_rest_are_written(self):
        """End to end: the right draft is first, the other three take three seconds."""
        writer = Slowly([RIGHT, WRONG, WRONG, WRONG], delay=3.0)
        agent = self.agent(writer, 4)
        started = time.time()
        outcome = agent.step(1)
        spent = time.time() - started
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.finished, outcome.reason)
        self.assertIn("return 42", self.on_disk("work.py"))
        self.assertLess(spent, 3.0, "the agent waited out the stragglers")
        self.assertGreaterEqual(writer.abandoned, 1,
                                "the writer was never told the answer was known")

    def test_the_winner_is_not_run_a_second_time(self):
        """The trial already ran this code against this command; that is the check."""
        agent = self.agent(Slowly([RIGHT, WRONG], delay=0.0), 2)
        agent.step(1)
        # One baseline before anything was written, and one check for the
        # candidate that was applied. The trials themselves run in copies.
        self.assertEqual(len(agent.checks), 1)
        self.assertTrue(agent.checks[0]["ok"])

    def test_a_writer_that_cannot_stream_still_works(self):
        """Older writers, and any stand-in, fall back to the pile-then-race path."""

        class PileOnly:
            def __init__(self):
                self.usage = WriterUsage()

            def drafts(self, prompt, n=4, system="", max_tokens=1600, spread=0.25,
                       enough=0, grace=2.5):
                return [Draft(0, "```\n%s```" % WRONG, WRONG, 0.01),
                        Draft(1, "```\n%s```" % RIGHT, RIGHT, 0.01)]

        self.repo.write("Makefile", "test:\n\t@grep -q 'return 42' work.py\n")
        agent = self.agent(PileOnly(), 2)
        outcome = agent.step(1)
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.finished, outcome.reason)
        self.assertIn("return 42", self.on_disk("work.py"))


class Straggling(unittest.TestCase):
    """Nothing green is still a possible ending, and it must not get slower."""

    def writer(self, fast: int, slow: int, delay: float):
        from jevcode.writer import Writer

        writer = Writer(url="http://example.invalid", key="x", model="m")

        def one(i, n, prompt, system, spread, max_tokens):
            if i >= fast:
                time.sleep(delay)
            return Draft(i, "", "code %d" % i, 0.01)

        writer._one_draft = one
        return writer

    def test_the_stragglers_are_left_behind_once_enough_have_landed(self):
        writer = self.writer(fast=2, slow=2, delay=10.0)
        started = time.time()
        got = list(writer.stream("p", n=4, enough=2, grace=0.2))
        self.assertEqual(len(got), 2)
        self.assertLess(time.time() - started, 5.0)

    def test_waiting_for_all_of_them_is_still_possible(self):
        writer = self.writer(fast=2, slow=1, delay=0.3)
        got = list(writer.stream("p", n=3, enough=0))
        self.assertEqual(len(got), 3)


class Stopping(unittest.TestCase):
    def test_a_command_is_killed_when_the_answer_is_already_known(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        started = time.time()
        run = act.run("sleep 20", tempfile.gettempdir(), timeout=30, stop=stop)
        self.assertTrue(run.abandoned)
        self.assertFalse(run.ok)
        self.assertLess(time.time() - started, 5.0)

    def test_a_stoppable_run_still_reports_what_the_command_said(self):
        stop = threading.Event()
        run = act.run("echo hello && exit 3", tempfile.gettempdir(), timeout=30, stop=stop)
        self.assertEqual(run.code, 3)
        self.assertIn("hello", run.output)
        self.assertFalse(run.abandoned)

    def test_output_longer_than_a_pipe_does_not_wedge_the_run(self):
        """A suite that prints a megabyte used to fill the pipe and hang forever."""
        stop = threading.Event()
        run = act.run("python3 -c \"print('x' * 2000000)\"", tempfile.gettempdir(),
                      timeout=30, stop=stop)
        self.assertTrue(run.ok)
        self.assertGreater(len(run.output), 1900000)

    def test_a_stoppable_run_times_out_like_any_other(self):
        stop = threading.Event()
        run = act.run("sleep 20", tempfile.gettempdir(), timeout=1, stop=stop)
        self.assertTrue(run.timed_out)
        self.assertFalse(run.ok)


if __name__ == "__main__":
    unittest.main()
