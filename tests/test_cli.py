"""The parts a person touches: settings, sessions, permissions, the prompt line.

No network anywhere in here. Everything that would normally reach a model goes
through the fakes, which is the point: the terminal, the undo stack and the
argument parsing are worth testing on every commit, and none of them need a key.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jevcode import act, config, locate, permission, repl, session  # noqa: E402
from jevcode.cli import parser, split_project                        # noqa: E402
from jevcode.engine import Agent                                     # noqa: E402
from jevcode.repo import Repo                                        # noqa: E402
from jevcode.trace import Trace                                      # noqa: E402
from jevcode.ui import Ui                                            # noqa: E402

from fakes import FakeOne, FakeWriter                                # noqa: E402


class Sandbox(unittest.TestCase):
    """Every test gets its own project directory, config home and data home."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jevcode-test-")
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(self.root)
        self._env = dict(os.environ)
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "cfg")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "data")
        for name in ("JEVCODE_JEV_MODEL", "JEVCODE_WRITER_MODEL",
                     "JEVCODE_SYSTEMONE_URL", "JEVCODE_WRITER_URL"):
            os.environ.pop(name, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, rel: str) -> str:
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return fh.read()

    def put(self, rel: str, text: str) -> str:
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class Arguments(unittest.TestCase):
    def test_bare_directory_is_not_a_subcommand(self):
        self.assertEqual(split_project(["run", "fix it"]), ("", ["run", "fix it"]))
        self.assertEqual(split_project(["../elsewhere"]), ("../elsewhere", []))
        self.assertEqual(split_project(["-c"]), ("", ["-c"]))

    def test_a_flag_before_the_subcommand_survives_it(self):
        args = parser().parse_args(["-C", "/tmp", "run", "do a thing"])
        self.assertEqual(args.directory, "/tmp")
        self.assertEqual(args.command, "run")

    def test_a_flag_after_the_subcommand_wins(self):
        args = parser().parse_args(["-n", "2", "run", "-n", "9", "x"])
        self.assertEqual(args.candidates, 9)


class Settings(Sandbox):
    def test_project_file_beats_user_file(self):
        os.makedirs(config.user_dir())
        with open(config.user_file(), "w") as fh:
            json.dump({"candidates": 3, "max_steps": 5}, fh)
        self.put(".jevcode.json", json.dumps({"candidates": 8}))
        cfg = config.Config(self.root)
        self.assertEqual(cfg["candidates"], 8)
        self.assertEqual(cfg["max_steps"], 5)

    def test_environment_beats_both(self):
        self.put(".jevcode.json", json.dumps({"writer_model": "from-file"}))
        os.environ["JEVCODE_WRITER_MODEL"] = "from-env"
        self.assertEqual(config.Config(self.root)["writer_model"], "from-env")

    def test_flags_beat_everything_but_only_when_given(self):
        cfg = config.Config(self.root).apply(candidates=None, writer_model="typed")
        self.assertEqual(cfg["writer_model"], "typed")
        self.assertEqual(cfg["candidates"], 6)

    def test_comments_do_not_break_the_file(self):
        self.put(".jevcode.json", '// mine\n{"candidates": 2}\n')
        self.assertEqual(config.Config(self.root)["candidates"], 2)

    def test_project_rules_are_picked_up(self):
        self.put("AGENTS.md", "# rules\nNever touch vendor/.")
        self.assertIn("Never touch vendor/", config.Config(self.root).instructions())

    def test_keys_come_from_the_environment_first(self):
        config.save_credential("writer", "from-file")
        self.assertEqual(config.key_for("writer"), "from-file")
        os.environ["JEVCODE_WRITER_KEY"] = "from-env"
        self.assertEqual(config.key_for("writer"), "from-env")


class Sessions(Sandbox):
    def test_a_turn_survives_a_reload(self):
        s = session.Session(self.root)
        s.add("do a thing", "done", [], {"decisions": 4, "cost": 0.5})
        again = session.Session.load(self.root, s.id)
        self.assertEqual(again.turns[0]["prompt"], "do a thing")
        self.assertEqual(again.spent()["decisions"], 4)

    def test_undo_puts_the_file_back_and_redo_returns_it(self):
        path = self.put("a.py", "one\n")
        repo = Repo(self.root)
        edit = act.apply_edit(repo, "a.py", 1, 1, "two")
        s = session.Session(self.root)
        s.add("change it", "done", [edit], {})
        self.assertEqual(self.read("a.py").strip(), "two")

        turn, restored = s.undo()
        self.assertEqual(restored, ["a.py"])
        self.assertEqual(self.read("a.py"), "one\n")

        s.redo()
        self.assertEqual(self.read("a.py").strip(), "two")

    def test_undoing_a_created_file_removes_it(self):
        repo = Repo(self.root)
        edit = act.create_file(repo, "new.py", "x = 1")
        edit.before = ""
        s = session.Session(self.root)
        s.add("make it", "done", [edit], {})
        s.undo()
        self.assertFalse(os.path.exists(os.path.join(self.root, "new.py")))

    def test_sessions_are_listed_newest_first(self):
        first = session.Session(self.root)
        first.add("older", "done", [], {})
        second = session.Session(self.root)
        second.add("newer", "done", [], {})
        rows = session.listing(self.root)
        self.assertEqual(rows[0]["id"], second.id)
        self.assertEqual(len(rows), 2)

    def test_a_new_turn_ends_the_redo_branch(self):
        s = session.Session(self.root)
        s.add("one", "done", [], {})
        s.undo()
        s.add("two", "done", [], {})
        self.assertEqual(s.undone, [])


class Permissions(unittest.TestCase):
    def test_allow_and_deny_never_ask(self):
        self.assertTrue(permission.Permission("allow", ui=_NeverAsk()).allows("run", "ls"))
        self.assertFalse(permission.Permission("deny", ui=_NeverAsk()).allows("run", "ls"))

    def test_always_is_remembered_for_the_kind(self):
        p = permission.Permission("ask", ui=_Answers([1]))
        self.assertTrue(p.allows("run", "pytest -q"))
        self.assertTrue(p.allows("run", "make test"))     # not asked again

    def test_no_means_no(self):
        p = permission.Permission("ask", ui=_Answers([2]))
        self.assertFalse(p.allows("edit", "a.py"))


class _NeverAsk:
    def ask(self, *a, **k):
        raise AssertionError("should not have asked")

    def say(self, *a, **k):
        pass

    def diff(self, *a, **k):
        pass


class _Answers:
    def __init__(self, answers):
        self.answers = list(answers)

    def ask(self, *a, **k):
        return self.answers.pop(0)

    def say(self, *a, **k):
        pass

    def diff(self, *a, **k):
        pass


class Mentions(Sandbox):
    def test_at_sign_finds_the_file_by_its_tail(self):
        self.put("src/deep/parser.py", "x = 1\n")
        repo = Repo(self.root)
        prompt, attached = repl.expand("fix @parser.py please", self.root, repo)
        self.assertEqual(attached, ["src/deep/parser.py"])
        self.assertIn("src/deep/parser.py", prompt)

    def test_an_unknown_file_is_left_as_written(self):
        repo = Repo(self.root)
        prompt, attached = repl.expand("look at @nope.py", self.root, repo)
        self.assertEqual(attached, [])
        self.assertIn("nope.py", prompt)


class NewFiles(Sandbox):
    def test_a_path_named_in_the_task_comes_first(self):
        self.put("app/main.py", "x = 1\n")
        options = locate.new_file_options(Repo(self.root), "add app/cache.py with an LRU")
        self.assertEqual(list(options)[0], "app/cache.py")

    def test_paths_that_exist_are_not_offered(self):
        self.put("app/main.py", "x = 1\n")
        options = locate.new_file_options(Repo(self.root), "rewrite app/main.py")
        self.assertNotIn("app/main.py", options)


class Loop(Sandbox):
    def test_the_agent_edits_creates_and_stops(self):
        self.put("a.py", "value = 1\n")
        one = FakeOne(script=["read", "edit"], done_after=2)
        agent = Agent("make value two", self.root, one=one, writer=FakeWriter(),
                      trace=Trace(quiet=True), allow_commands=False, candidates=2)
        outcome = agent.run()
        self.assertTrue(outcome.finished)
        self.assertEqual(self.read("a.py").strip(), "value = 2")

    def test_a_declined_edit_changes_nothing(self):
        self.put("a.py", "value = 1\n")
        one = FakeOne(script=["read", "edit"], done_after=2)
        agent = Agent("make value two", self.root, one=one, writer=FakeWriter(),
                      trace=Trace(quiet=True), allow_commands=False, candidates=2,
                      permit=permission.Permission("deny"))
        agent.run()
        self.assertEqual(self.read("a.py"), "value = 1\n")

    def test_project_rules_reach_the_model(self):
        self.put("a.py", "value = 1\n")
        one = FakeOne(script=["read"], done_after=99)
        agent = Agent("x", self.root, one=one, writer=FakeWriter(),
                      trace=Trace(quiet=True), allow_commands=False, max_steps=1,
                      instructions="Never touch vendor/")
        agent.run()
        self.assertIn("project_rules", agent.state())


class Demo(unittest.TestCase):
    def test_the_tour_fixes_its_own_sample_project(self):
        from jevcode.demo import DemoOne, DemoWriter, playground
        root = playground()
        agent = Agent("the counter should start at zero, fix counter.py", root,
                      one=DemoOne(pause=0), writer=DemoWriter(pause=0),
                      trace=Trace(quiet=True), candidates=4)
        outcome = agent.run()
        self.assertTrue(outcome.finished)
        with open(os.path.join(root, "counter.py"), encoding="utf-8") as fh:
            self.assertIn("start=0", fh.read())


class Terminal(unittest.TestCase):
    def test_colour_is_off_when_nobody_is_watching(self):
        ui = Ui(color=False)
        self.assertEqual(ui.paint("x", "red"), "x")

    def test_the_diff_renderer_keeps_every_line(self):
        out = io.StringIO()
        stdout, sys.stdout = sys.stdout, out
        try:
            Ui(color=False).diff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-one\n+two\n")
        finally:
            sys.stdout = stdout
        self.assertEqual(len(out.getvalue().strip().splitlines()), 5)


if __name__ == "__main__":
    unittest.main()
