"""The conversation: one prompt, one run of the agent, repeat.

What makes this usable rather than merely runnable is the boring half — the
things every good terminal agent has and no demo script does. History between
runs. `@file` to point at something without typing a path. `!command` to look
at the machine yourself without leaving. `/undo` that actually puts the file
back. A question before anything is written or run, and a way to say "stop
asking". None of it is clever; all of it is the difference between a toy and a
tool.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from . import act, locate, session as sessions
from .config import Config, key_for, write_instructions
from .engine import Agent
from .permission import Permission
from .repo import Repo
from .systemone import SystemOne, Usage
from .trace import Trace
from .ui import Line, Ui
from .writer import Writer, WriterUsage

BANNER = r"""   _            _
  (_) _____   _(_) ___ ___   __| | ___
  | |/ _ \ \ / / |/ __/ _ \ / _` |/ _ \
  | |  __/\ V /| | (_| (_) | (_| |  __/
 _/ |\___| \_/ |_|\___\___/ \__,_|\___|
|__/"""

COMMANDS = {
    "/help": "show this list",
    "/new": "start a new session (alias /clear)",
    "/sessions": "list this project's sessions and switch (alias /resume)",
    "/undo": "undo the last change, on disk",
    "/redo": "redo what /undo took back",
    "/diff": "what has changed in this session",
    "/cost": "decisions, requests, time and money so far",
    "/models": "which models are answering",
    "/model": "switch the writer: /model <name>",
    "/details": "show or hide each decision as it is made",
    "/permission": "ask | allow | deny before edits and commands",
    "/init": "write an AGENTS.md for this project",
    "/export": "write the conversation out as Markdown",
    "/editor": "compose the next message in $EDITOR",
    "/compact": "forget the earlier turns, keep the files",
    "/exit": "leave (aliases /quit, /q)",
}

ALIASES = {"/clear": "/new", "/resume": "/sessions", "/continue": "/sessions",
           "/quit": "/exit", "/q": "/exit", "/summarize": "/compact", "/?": "/help"}


class Repl:
    def __init__(self, root: str, cfg: Config, ui: Ui, resume: str = ""):
        self.root = os.path.abspath(root)
        self.cfg = cfg
        self.ui = ui
        self.repo = Repo(self.root)
        self.permission = Permission(cfg.get("permission", "ask"), ui)
        self.session = self._open(resume)
        self.line = Line(sorted(list(COMMANDS) + list(ALIASES)), self._paths, ui)
        self.running = True

    # ----------------------------------------------------------- plumbing

    def _open(self, resume: str) -> sessions.Session:
        if resume == "last":
            found = sessions.Session.latest(self.root)
            if found:
                return found
        elif resume:
            return sessions.Session.load(self.root, resume)
        return sessions.Session(self.root)

    def _paths(self) -> list:
        try:
            return self.repo.files()
        except OSError:
            return []

    def greet(self) -> None:
        self.ui.say(self.ui.paint(BANNER, "cyan"))
        from . import __version__
        self.ui.say(self.ui.paint(
            "  v%s · %s decides · %s writes · %s"
            % (__version__, self.cfg["jev_model"], self.cfg["writer_model"],
               os.path.basename(self.root)), "grey"))
        if self.session.turns:
            self.ui.say(self.ui.paint("  resumed: %s (%d turns)"
                                      % (self.session.title, len(self.session.turns)),
                                      "grey"))
        if self.cfg.get("demo"):
            self.ui.say(self.ui.paint("  demo mode: answers are canned, no model is "
                                      "called and nothing is charged", "yellow"))
        self.ui.say(self.ui.paint("  /help for commands, @ for a file, ! for a shell "
                                  "command, Ctrl+C to interrupt", "grey"))
        self.ui.say()

    # --------------------------------------------------------------- loop

    def run(self) -> int:
        self.greet()
        while self.running:
            try:
                raw = self.line.read(self.ui.paint("› ", "cyan", "bold"))
            except EOFError:
                self.ui.say()
                break
            except KeyboardInterrupt:
                self.ui.say()
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                self.handle(raw)
            except KeyboardInterrupt:
                self.ui.done()
                self.ui.say()
                self.ui.warn("interrupted")
            except RuntimeError as ex:
                self.ui.done()
                self.ui.bad(str(ex))
        return 0

    def handle(self, raw: str) -> None:
        if raw.startswith("!"):
            return self.shell(raw[1:].strip())
        if raw.startswith("/"):
            word, _, rest = raw.partition(" ")
            word = ALIASES.get(word, word)
            handler = getattr(self, "cmd_" + word[1:].replace("-", "_"), None)
            if handler is None:
                self.ui.warn("no such command: %s — try /help" % word)
                return None
            return handler(rest.strip())
        return self.task(raw)

    # -------------------------------------------------------------- doing

    def shell(self, command: str) -> None:
        if not command:
            return
        self.ui.say(self.ui.paint("$ " + command, "grey"))
        result = act.run(command, self.root, timeout=300)
        body = result.output.rstrip()
        if body:
            self.ui.say(body if len(body) < 8000 else body[:8000] + "\n…")
        self.ui.say(self.ui.paint("exit %d · %.1fs" % (result.code, result.seconds), "grey"))
        self.session.note("!" + command, body[-2000:])

    def task(self, text: str) -> None:
        prompt, attached = expand(text, self.root, self.repo)
        for path in attached:
            self.ui.say(self.ui.paint("  + %s" % path, "grey"))

        usage, writer_usage = Usage(), WriterUsage()
        if self.cfg.get("demo"):
            from .demo import DemoOne, DemoWriter
            one, writer = DemoOne(usage), DemoWriter(writer_usage)
        else:
            one = SystemOne(url=self.cfg["systemone_url"], key=key_for("systemone"),
                            model=self.cfg["jev_model"], usage=usage)
            writer = Writer(url=self.cfg["writer_url"], key=key_for("writer"),
                            model=self.cfg["writer_model"], usage=writer_usage)
        trace = ReplTrace(self.ui, quiet=not self.cfg.get("details", True))
        agent = Agent(prompt, self.root, one=one, writer=writer, trace=trace,
                      max_steps=int(self.cfg["max_steps"]),
                      candidates=int(self.cfg["candidates"]),
                      permit=self.permission,
                      instructions=self.cfg.instructions(),
                      settle_at=int(self.cfg.get("settle_at") or 0),
                      settle_grace=float(self.cfg.get("settle_grace") or 2.5),
                      history=[t.get("prompt", "") for t in self.session.turns],
                      repo=self.repo)

        started = time.time()
        spinner = self.ui.working("thinking")
        trace.spinner = spinner
        try:
            outcome = agent.run()
            reason = ("done — " if outcome.finished else "stopped — ") + outcome.reason
        except KeyboardInterrupt:
            reason = "interrupted"
        finally:
            spinner.stop()
            self.ui.done()

        self.repo.forget()
        changed = _unique_edits(agent.edits)
        for path in changed:
            self.ui.good("changed %s" % path)
        self.ui.say(self.ui.paint(reason, "bold"))
        self.ui.say(self.ui.paint(_spent_line(usage, writer_usage, time.time() - started),
                                  "grey"))
        self.session.add(prompt, reason, agent.edits, {
            "decisions": usage.questions, "requests": usage.requests,
            "writer_calls": writer_usage.calls,
            "cost": usage.cost + writer_usage.cost,
            "currency": usage.currency or writer_usage.currency,
            "seconds": round(time.time() - started, 2),
        }, steps=getattr(agent, "steps", 0))

    # ----------------------------------------------------------- commands

    def cmd_help(self, _rest: str) -> None:
        self.ui.say()
        for name, what in COMMANDS.items():
            self.ui.say("  %s  %s" % (self.ui.paint("%-12s" % name, "cyan"), what))
        self.ui.say()
        self.ui.say(self.ui.paint("  @path/to/file   put a file in front of the agent",
                                  "grey"))
        self.ui.say(self.ui.paint("  !command        run a shell command yourself", "grey"))
        self.ui.say()

    def cmd_new(self, _rest: str) -> None:
        self.session = sessions.Session(self.root)
        self.ui.say(self.ui.paint("new session", "grey"))

    def cmd_sessions(self, rest: str) -> None:
        rows = sessions.listing(self.root)
        if not rows:
            return self.ui.say(self.ui.paint("no sessions here yet", "grey"))
        if rest:
            self.session = sessions.Session.load(self.root, rest)
            return self.ui.say(self.ui.paint("resumed %s" % self.session.title, "grey"))
        for row in rows[:15]:
            mark = "*" if row["id"] == self.session.id else " "
            self.ui.say("%s %s  %-10s  %s" % (
                mark, self.ui.paint(row["id"], "cyan"),
                sessions.ago(row["updated"]), row["title"][:60]))
        self.ui.say(self.ui.paint("  /sessions <id> to switch", "grey"))
        return None

    def cmd_undo(self, _rest: str) -> None:
        turn, restored = self.session.undo()
        if not turn:
            return self.ui.say(self.ui.paint("nothing to undo", "grey"))
        self.repo.forget()
        for path in restored:
            self.ui.good("put back %s" % path)
        return None

    def cmd_redo(self, _rest: str) -> None:
        turn, restored = self.session.redo()
        if not turn:
            return self.ui.say(self.ui.paint("nothing to redo", "grey"))
        self.repo.forget()
        for path in restored:
            self.ui.good("re-applied %s" % path)
        return None

    def cmd_diff(self, _rest: str) -> None:
        shown = 0
        for turn in self.session.turns:
            for edit in turn.get("edits") or []:
                text = act.diff(edit["before"], edit["after"], edit["path"])
                if text:
                    self.ui.diff(text)
                    shown += 1
        if not shown:
            self.ui.say(self.ui.paint("nothing changed in this session", "grey"))

    def cmd_cost(self, _rest: str) -> None:
        total = self.session.spent()
        self.ui.say("  %d decisions in %d requests · %d writer calls · %.1fs"
                    % (total["decisions"], total["requests"], total["writer_calls"],
                       total["seconds"]))
        if total["cost"]:
            self.ui.say("  %.4f %s" % (total["cost"], total["currency"]))

    def cmd_models(self, _rest: str) -> None:
        self.ui.say("  decides  %s  %s" % (self.ui.paint(self.cfg["jev_model"], "bold"),
                                           self.ui.paint(self.cfg["systemone_url"], "grey")))
        self.ui.say("  writes   %s  %s" % (self.ui.paint(self.cfg["writer_model"], "bold"),
                                           self.ui.paint(self.cfg["writer_url"], "grey")))

    def cmd_model(self, rest: str) -> None:
        if not rest:
            return self.cmd_models("")
        self.cfg["writer_model"] = rest
        return self.ui.say(self.ui.paint("the writer is now %s" % rest, "grey"))

    def cmd_details(self, rest: str) -> None:
        want = {"on": True, "off": False}.get(rest, not self.cfg.get("details", True))
        self.cfg["details"] = want
        self.ui.say(self.ui.paint("details %s" % ("on" if want else "off"), "grey"))

    def cmd_permission(self, rest: str) -> None:
        if rest in ("ask", "allow", "deny"):
            self.permission.policy = rest
            self.permission.remembered.clear()
        self.ui.say(self.ui.paint("permission: %s" % self.permission.policy, "grey"))

    def cmd_init(self, _rest: str) -> None:
        path = write_instructions(self.root, "", self.repo.known_commands())
        self.ui.good("wrote %s — fill in what the project is" % path)

    def cmd_export(self, rest: str) -> None:
        target = rest or os.path.join(self.root, "jevcode-%s.md" % self.session.id)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(self.session.transcript())
        self.ui.good("wrote %s" % target)

    def cmd_editor(self, _rest: str) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor:
            return self.ui.warn("set $EDITOR first")
        import tempfile
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as fh:
            path = fh.name
        subprocess.call("%s %s" % (editor, path), shell=True)
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        os.unlink(path)
        if text:
            self.ui.say(self.ui.paint("› " + text.splitlines()[0][:70], "grey"))
            self.task(text)
        return None

    def cmd_compact(self, _rest: str) -> None:
        kept = self.session.turns[-1:]
        self.session.turns = kept
        self.session.save()
        self.ui.say(self.ui.paint("kept the last turn, forgot the rest", "grey"))

    def cmd_exit(self, _rest: str) -> None:
        self.running = False


# ----------------------------------------------------------------- helpers

def expand(text: str, root: str, repo) -> tuple:
    """Turn `@path` into a path the agent will find, and say what was attached."""
    words, attached = [], []
    for word in text.split():
        if not word.startswith("@") or len(word) < 2:
            words.append(word)
            continue
        wanted = word[1:]
        match = _closest(wanted, repo)
        if match:
            words.append(match)
            attached.append(match)
        else:
            words.append(wanted)
    return " ".join(words), attached


def _closest(wanted: str, repo) -> str:
    try:
        files = repo.files()
    except OSError:
        return ""
    if wanted in files:
        return wanted
    low = wanted.lower()
    exact = [f for f in files if f.lower().endswith(low)]
    if exact:
        return min(exact, key=len)
    fuzzy = [f for f in files if low in f.lower()]
    return min(fuzzy, key=len) if fuzzy else ""


def _unique_edits(edits: list) -> list:
    seen, out = set(), []
    for edit in edits:
        if edit.path not in seen:
            seen.add(edit.path)
            out.append(edit.path)
    return out


def _spent_line(usage, writer_usage, seconds: float) -> str:
    money = ""
    if usage.cost or writer_usage.cost:
        money = " · %.4f %s" % (usage.cost + writer_usage.cost,
                                usage.currency or writer_usage.currency or "")
    return ("%d decisions in %d requests · %d drafts · %.1fs%s"
            % (usage.questions, usage.requests, writer_usage.calls, seconds, money))


class ReplTrace(Trace):
    """The agent's own log, drawn by the terminal's renderer instead of `print`."""

    def __init__(self, ui: Ui, quiet: bool = False, path: str | None = None):
        super().__init__(path=path, quiet=quiet, color=ui.color)
        self.ui = ui
        self.spinner = None

    def say(self, text: str = "") -> None:
        if not self.quiet:
            self.ui.say(text)

    def step(self, number: int, action: str, probability: float, confidence: float) -> None:
        if self.spinner:
            self.spinner.update(label=action)
        if not self.quiet:
            self.ui.step(action, self.ui.paint("step %d" % number, "grey"), probability)

    def detail(self, text: str) -> None:
        if not self.quiet:
            self.ui.detail(text)

    def good(self, text: str) -> None:
        if not self.quiet:
            self.ui.good(text)

    def bad(self, text: str) -> None:
        if not self.quiet:
            self.ui.bad(text)

    def options(self, ranked: list, limit: int = 3) -> None:
        if self.quiet:
            return
        self.ui.say("  " + self.ui.paint(
            "  ".join("%s %.2f" % (name, p) for name, p in ranked[:limit]), "grey"))
