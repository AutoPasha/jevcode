"""The README may not disagree with the benchmark it quotes.

Three ways a published number goes wrong, one test each:

* the picture is left over from an older run, so it shows numbers the results
  file no longer contains;
* the table was edited by hand and drifted;
* a contestant that never started was written down as a zero, and a bench that
  scores a missing binary 0/9 is not a bench.

And one about the picture itself: a chart where every bar is the same length
is not a comparison, it is nine rows of wallpaper. It does not get published.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")
sys.path.insert(0, BENCH)

import chart      # noqa: E402
import compare    # noqa: E402
import publish    # noqa: E402


def payload(runs):
    return {"when": "2026-01-01 00:00", "note": "",
            "tasks": sorted({r["task"] for r in runs}),
            "agents": sorted({r["agent"] for r in runs}), "runs": runs}


def run(agent, task, passed=True, seconds=10.0, **extra):
    row = {"agent": agent, "task": task, "attempt": 1, "passed": passed,
           "seconds": seconds, "cost": 0.0, "currency": "RUB", "decisions": 0,
           "requests": 0, "drafts": 0, "note": ""}
    row.update(extra)
    return row


class EveryBarTheSame(unittest.TestCase):
    def test_identical_series_are_not_a_chart(self):
        both = [{"name": "a", "values": [100, 100, 100]},
                {"name": "b", "values": [100, 100, 100]}]
        self.assertFalse(chart.useful(both))

    def test_one_disagreement_is_enough(self):
        both = [{"name": "a", "values": [100, 100, 100]},
                {"name": "b", "values": [100, 0, 100]}]
        self.assertTrue(chart.useful(both))

    def test_a_flat_measure_is_not_published(self):
        runs = [run(agent, task) for agent in ("ours", "theirs")
                for task in ("one", "two", "three")]
        with tempfile.TemporaryDirectory() as out:
            written = [os.path.basename(p) for p in chart.charts(payload(runs), out)]
        self.assertNotIn("solved.svg", written)
        self.assertIn("scoreboard.svg", written)

    def test_a_measure_that_separates_them_is_published(self):
        runs = [run("ours", "one"), run("ours", "two"), run("ours", "three"),
                run("theirs", "one"), run("theirs", "two", passed=False),
                run("theirs", "three")]
        with tempfile.TemporaryDirectory() as out:
            written = [os.path.basename(p) for p in chart.charts(payload(runs), out)]
        self.assertIn("solved.svg", written)


class NobodyWinsByNotReporting(unittest.TestCase):
    def test_a_missing_number_is_never_the_best_cell(self):
        runs = [run("ours", "one", requests=2, drafts=3),
                run("theirs", "one")]
        with tempfile.TemporaryDirectory() as out:
            chart.charts(payload(runs), out)
            body = open(os.path.join(out, "scoreboard.svg"), encoding="utf-8").read()
        self.assertIn("not reported", body)


class ContestantThatNeverStarted(unittest.TestCase):
    def test_a_missing_binary_is_not_a_failed_task(self):
        with tempfile.TemporaryDirectory() as directory:
            result = compare.run_command(
                directory, "anything",
                {"command": "/no/such/agent --dir {dir} \"{task}\""}, timeout=30)
        self.assertTrue(result["broken"], result)

    def test_a_contestant_that_runs_is_not_broken(self):
        with tempfile.TemporaryDirectory() as directory:
            result = compare.run_command(
                directory, "anything", {"command": "true"}, timeout=30)
        self.assertFalse(result["broken"], result)


class TaskLibraryIsNotTheSandbox(unittest.TestCase):
    """A contestant that edits the originals instead of its copy is caught."""

    def test_an_untouched_library_looks_untouched(self):
        with tempfile.TemporaryDirectory() as tasks:
            os.makedirs(os.path.join(tasks, "one"))
            open(os.path.join(tasks, "one", "code.py"), "w").write("x = 1\n")
            before = compare.fingerprint(tasks)
            self.assertEqual(compare.library_changed(tasks, before), [])

    def test_an_edited_original_is_named(self):
        with tempfile.TemporaryDirectory() as tasks:
            os.makedirs(os.path.join(tasks, "one"))
            path = os.path.join(tasks, "one", "code.py")
            open(path, "w").write("x = 1\n")
            before = compare.fingerprint(tasks)
            open(path, "w").write("x = 2\n")
            self.assertEqual(compare.library_changed(tasks, before),
                             [os.path.join("one", "code.py")])

    def test_a_new_file_counts_too(self):
        with tempfile.TemporaryDirectory() as tasks:
            os.makedirs(os.path.join(tasks, "one"))
            before = compare.fingerprint(tasks)
            open(os.path.join(tasks, "one", "sneaked.py"), "w").write("")
            self.assertTrue(compare.library_changed(tasks, before))

    def test_caches_are_ignored(self):
        with tempfile.TemporaryDirectory() as tasks:
            os.makedirs(os.path.join(tasks, "one", "__pycache__"))
            before = compare.fingerprint(tasks)
            open(os.path.join(tasks, "one", "__pycache__", "c.pyc"), "w").write("")
            self.assertEqual(compare.library_changed(tasks, before), [])


class ReadmeMatchesTheResults(unittest.TestCase):
    """The committed README and charts are what bench/publish.py would write."""

    def test_nothing_is_stale(self):
        done = subprocess.run([sys.executable, os.path.join(BENCH, "publish.py"),
                               "--check"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0,
                         "README or charts disagree with bench/results.json; "
                         "run `python3 bench/publish.py`\n" + done.stdout + done.stderr)

    def test_every_chart_the_readme_shows_exists(self):
        readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        for name in sorted(set(__import__("re").findall(
                r"docs/assets/([A-Za-z0-9_.-]+\.svg)", readme))):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "docs", "assets", name)),
                            "README shows %s, which is not in docs/assets" % name)

    def test_the_table_is_generated_not_typed(self):
        results = json.load(open(os.path.join(BENCH, "results.json"), encoding="utf-8"))
        readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
        self.assertIn(publish.OPEN, readme, "README has no benchmark block")
        # assertIn would print the whole README on failure; the message has to
        # be the table, not the file it was looked for in.
        wanted = publish.block(results)
        if wanted not in readme:
            self.fail("the block between the markers is not what "
                      "bench/publish.py builds from bench/results.json:\n\n"
                      + wanted)


if __name__ == "__main__":
    unittest.main()
