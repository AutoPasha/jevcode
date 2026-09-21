"""A project with no test suite is still a project the agent has to finish.

What these cover is one failure seen whole on a real task — "make a landing
page: index.html and style.css". The agent wrote index.html ten times in a row,
each pass overwriting the last, never wrote the stylesheet and stopped only
when it ran out of steps. Three things were wrong and each has a test here: it
could not see files it had created itself, `create` was allowed to land on a
file that exists, and "done" was a question about a green command in a
repository where there is no command to run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from jevcode import markup, questions
from jevcode.engine import Agent
from jevcode.repo import Repo

from fakes import FakeOne, FakeWriter


PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Beans</title>
<link rel="stylesheet" href="style.css"></head>
<body><h1>Beans</h1><p>Coffee, monthly.</p></body>
</html>
"""


def _git(root, *args):
    subprocess.run(["git", "-C", root] + list(args), capture_output=True, check=True)


class Tree(unittest.TestCase):
    """What the repository lists is what the agent can reason about."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevpage-")
        _git(self.root, "init", "-q", ".")
        self.write("README.md", "# landing\n")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_a_file_git_does_not_track_yet_is_still_a_file(self):
        self.write("index.html", PAGE)
        self.assertIn("index.html", Repo(self.root).files())

    def test_an_existing_path_is_not_offered_as_a_new_one(self):
        from jevcode import locate
        self.write("index.html", PAGE)
        repo = Repo(self.root)
        options = locate.new_file_options(repo, "index.html with a hero and style.css")
        self.assertNotIn("index.html", options)
        self.assertIn("style.css", options)

    def test_ignored_files_stay_ignored(self):
        self.write(".gitignore", "secret.txt\n")
        self.write("secret.txt", "shh\n")
        self.assertNotIn("secret.txt", Repo(self.root).files())


class PageCheck(unittest.TestCase):
    """The facts that stand in for a test suite."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevmarkup-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_unclosed_tag_is_caught(self):
        self.assertIn("never closed", markup.syntax("a.html", "<div><p>hi</p>"))

    def test_void_and_optional_tags_are_not_mistakes(self):
        self.assertEqual("", markup.syntax("a.html", PAGE))
        self.assertEqual("", markup.syntax("a.html", "<ul><li>one<li>two</ul>"))

    def test_unbalanced_css_is_caught(self):
        self.assertIn("never closed", markup.syntax("a.css", "body { color: red;\n"))
        self.assertEqual("", markup.syntax("a.css", "body { color: red; }\n"))

    def test_a_missing_stylesheet_is_a_problem(self):
        self.write("index.html", PAGE)
        found = markup.problems(Repo(self.root), "index.html")
        self.assertEqual(1, len(found))
        self.assertIn("style.css", found[0])

    def test_a_page_whose_files_are_all_there_is_clean(self):
        self.write("index.html", PAGE)
        self.write("style.css", "body { margin: 0; }\n")
        self.assertEqual([], markup.problems(Repo(self.root), "index.html"))

    def test_classes_with_no_rules_are_listed_but_not_called_problems(self):
        self.write("index.html",
                   '<html><head><link rel="stylesheet" href="style.css"></head>'
                   '<body><div class="hero wide"><p class="lead">hi</p></div></body></html>')
        self.write("style.css", ".hero { color: red; }\n")
        repo = Repo(self.root)
        self.assertEqual([], markup.problems(repo, "index.html"))
        self.assertEqual(["wide", "lead"], markup.unstyled(repo, "index.html"))

    def test_a_page_with_no_stylesheet_is_not_nagged_about_classes(self):
        self.write("index.html", '<html><body><div class="hero">hi</div></body></html>')
        self.assertEqual([], markup.unstyled(Repo(self.root), "index.html"))

    def test_pictures_from_another_domain_are_named(self):
        self.write("index.html",
                   '<html><body><img src="https://via.placeholder.com/80">'
                   '<img src="logo.svg"></body></html>')
        self.write("logo.svg", "<svg/>")
        repo = Repo(self.root)
        self.assertEqual([], markup.problems(repo, "index.html"))
        self.assertEqual(["https://via.placeholder.com/80"],
                         markup.remote_pictures(repo, "index.html"))

    def test_a_background_from_another_domain_counts_too(self):
        self.write("style.css",
                   ".hero { background: url('https://images.example.com/a.jpg'); }")
        self.assertEqual(["https://images.example.com/a.jpg"],
                         markup.remote_pictures(Repo(self.root), "style.css"))

    def test_external_and_anchor_links_are_left_alone(self):
        self.write("index.html",
                   '<html><body><a href="#pricing">p</a>'
                   '<a href="https://example.com/x.css">x</a>'
                   '<img src="data:image/png;base64,AA"></body></html>')
        self.assertEqual([], markup.problems(Repo(self.root), "index.html"))


class Finishing(unittest.TestCase):
    """"Done" asks about what can actually be established here."""

    def test_with_a_suite_it_still_asks_for_a_green_run(self):
        q = questions.step(files={}, regions={}, commands={"make test": "tests"},
                           queries={}, has_open_file=False, has_edits=True)
        self.assertIn("confirmed by a command", q["done"]["instructions"]["question"])

    def test_without_one_it_asks_about_the_code_and_the_page_check(self):
        q = questions.step(files={}, regions={}, commands={}, queries={},
                           has_open_file=False, has_edits=True, has_pages=True)
        self.assertNotIn("command", q["done"]["instructions"]["question"])
        self.assertIn("page_check", q["done"]["instructions"]["question"])


class Writing(unittest.TestCase):
    """The loop on a repository that declares no commands at all."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jevloop-")
        _git(self.root, "init", "-q", ".")
        with open(os.path.join(self.root, "README.md"), "w") as fh:
            fh.write("# landing\n")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def agent(self, script, writer_code):
        return Agent("make index.html with a hero, plus style.css", self.root,
                     one=FakeOne(script=script, done_after=99),
                     writer=FakeWriter(writer_code), max_steps=len(script))

    def test_create_does_not_land_on_a_file_that_exists(self):
        agent = self.agent(["create", "create"], PAGE)
        agent.run()
        made = [e.path for e in agent.edits]
        self.assertEqual(["index.html"], made[:1])
        # The second `create` found index.html already there and opened it
        # instead of writing another copy over the top of the first.
        self.assertNotEqual(["index.html", "index.html"], made)
        self.assertEqual(PAGE.strip(), open(
            os.path.join(self.root, "index.html")).read().strip())

    def test_the_missing_stylesheet_shows_up_in_the_history(self):
        agent = self.agent(["create"], PAGE)
        agent.run()
        said = " ".join(agent.history)
        self.assertIn("style.css", said)
        self.assertIn("does not exist", said)

    def test_the_page_check_is_part_of_what_the_model_sees(self):
        agent = self.agent(["create"], PAGE)
        agent.run()
        state = agent.state()
        self.assertIn("page_check", state)
        self.assertIn("index.html", state["page_check"]["problems"])


if __name__ == "__main__":
    unittest.main()
