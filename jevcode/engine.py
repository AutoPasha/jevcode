"""The loop.

One request to System One per step, and that request contains every question
the step might need: what to do, in which file, which region, which command,
and whether each possible action would even produce anything new. Most of those
answers are thrown away. They are speculative on purpose — a hundred extra
questions cost about as much as one, so the agent thinks about all its options
before every move instead of committing to the first plausible one.

What the loop does with those numbers is ordinary code: pick the action with
the best expected value, do it, write down what happened. The model never
executes anything and never sees a tool call; the code never guesses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import act, gate, locate, patch, questions
from .repo import Repo
from .systemone import SystemOne, Usage
from .trace import Trace
from .writer import Writer, WriterUsage

MAX_HISTORY = 14
OPEN_WINDOW = 160


@dataclass
class Outcome:
    finished: bool = False
    reason: str = ""
    steps: int = 0
    edits: list = field(default_factory=list)
    checks: list = field(default_factory=list)


class Agent:
    def __init__(self, task: str, root: str = ".", one: SystemOne | None = None,
                 writer: Writer | None = None, trace: Trace | None = None,
                 max_steps: int = 24, candidates: int = 6, dry_run: bool = False,
                 allow_commands: bool = True, permit=None, instructions: str = "",
                 settle_at: int = 0, settle_grace: float = 2.5, history: list | None = None,
                 repo: Repo | None = None):
        self.task = task
        self.repo = repo or Repo(root)
        self.one = one or SystemOne()
        self.writer = writer or Writer()
        self.trace = trace or Trace()
        self.max_steps = max_steps
        self.candidates = candidates
        self.dry_run = dry_run
        self.allow_commands = allow_commands
        self.permit = permit
        self.instructions = instructions
        self.settle_at = settle_at
        self.settle_grace = settle_grace
        self.earlier = list(history or [])

        self.history: list = []
        self.open_path: str | None = None
        self.open_focus: tuple = (1, OPEN_WINDOW)
        self.opened: dict = {}          # every file read so far → its focus
        self.edits: list = []
        self.checks: list = []
        self.searched: set = set()
        self.broken_commands: set = set()
        self.last_failure: tuple = ()
        self.failed_regions: dict = {}
        self._shortlist: dict | None = None

    # ---------------------------------------------------------------- state

    def shortlist(self) -> dict:
        if self._shortlist is None:
            self._shortlist = locate.shortlist(self.repo, self.task)
        return self._shortlist

    def state(self) -> dict:
        """What Jev sees. Small on purpose: unrelated context costs accuracy."""
        state = {"task": self.task, "history": self.history[-MAX_HISTORY:]}
        if self.instructions:
            state["project_rules"] = self.instructions[:6000]
        if self.earlier:
            state["earlier_in_this_conversation"] = self.earlier[-6:]
        if self.open_path:
            start, end = self.open_focus
            state["open_file"] = {
                "path": self.open_path,
                "body": self.repo.numbered(self.open_path, start, end),
            }
        others = [p for p in self.opened if p != self.open_path]
        if others:
            state["also_open"] = {p: self.repo.numbered(p, 1, 60) for p in others[-2:]}
        if self.edits:
            state["edits_made"] = ["%s lines %d-%d" % (e.path, e.start, e.end)
                                   for e in self.edits]
        if self.checks:
            state["last_check"] = self.checks[-1]
        return state

    def related_code(self, path: str) -> str:
        """Another file the change has to keep working with — usually its test.

        The writer gets this alongside the region. It is the difference between
        guessing at a signature and reading the call that has to succeed.
        """
        stem = os.path.splitext(os.path.basename(path))[0]
        for candidate in list(self.opened) + self.repo.files():
            if candidate == path:
                continue
            name = os.path.basename(candidate)
            if ("test" in name or "spec" in name) and stem in self.repo.read(candidate):
                return "%s:\n%s" % (candidate, self.repo.read(candidate))
        return ""

    def note(self, text: str) -> None:
        self.history.append("step %d: %s" % (len(self.history) + 1, text))

    # ----------------------------------------------------------------- loop

    def run(self) -> Outcome:
        for number in range(1, self.max_steps + 1):
            outcome = self.step(number)
            if outcome is not None:
                outcome.steps = number
                outcome.edits = self.edits
                outcome.checks = self.checks
                return outcome
        return Outcome(False, "ran out of steps (%d)" % self.max_steps,
                       self.max_steps, self.edits, self.checks)

    def step(self, number: int) -> Outcome | None:
        files = self.shortlist()
        regions = self._region_options()
        commands = self.repo.known_commands() if self.allow_commands else {}
        queries = {term: None for term in locate.search_terms(self.task)
                   if term not in self.searched}

        answers = self.one.ask(self.state(), questions.step(
            files=files, regions=regions, commands=commands, queries=queries,
            has_open_file=bool(self.open_path), has_edits=bool(self.edits)))

        if answers.p("needs_human") >= questions.HUMAN:
            self.trace.record("stop", why="needs_human", p=answers.p("needs_human"))
            return Outcome(False, "needs a decision only a person can make")
        unfinished = ("more_places" in answers
                      and answers.p("more_places") >= questions.MORE_PLACES)
        if answers.p("done") >= questions.DONE and self.edits and not unfinished:
            self.trace.record("stop", why="done", p=answers.p("done"))
            return Outcome(True, "task carried out and checked")

        action, ranked = self._choose(answers, unfinished)
        self.trace.step(number, action, dict(ranked).get(action, 0.0),
                        answers.confidence("action"))
        self.trace.options(ranked)
        self.trace.record("step", n=number, action=action, actions=dict(ranked),
                          confidence=answers.confidence("action"),
                          done=answers.p("done"),
                          enough_context=answers.p("enough_context"))

        handler = {
            "read": self._read, "search": self._search, "edit": self._edit,
            "create": self._create, "run": self._run, "finish": self._finish,
        }[action]
        return handler(answers)

    def _choose(self, answers, unfinished: bool = False) -> tuple:
        """Expected value, not the raw pick.

        `action` says which move looks right; `worth_*` says whether that move
        would actually produce anything the agent does not already have. A move
        that is popular but pointless — reading a file that is already open —
        loses to a less likely one that moves the task forward.
        """
        ranked = []
        for name, p in answers.ranked("action"):
            worth = answers.p("worth_" + name) if ("worth_" + name) in answers else 1.0
            weight = p * (0.25 + 0.75 * worth)
            if unfinished and name == "finish":
                # The change is not finished elsewhere: stopping is not an option
                # yet, however attractive it looks from here.
                weight *= 0.1
            ranked.append((name, round(weight, 4)))
        ranked.sort(key=lambda kv: -kv[1])
        action = ranked[0][0]
        if action == "edit" and not self.open_path:
            action = "read"
        if action == "run" and not self.allow_commands:
            action = "read"
        return action, ranked

    def _region_options(self) -> dict:
        if not self.open_path:
            return {}
        options = {}
        for r in self.repo.regions(self.open_path):
            options[r.label] = r.kind
            if len(options) >= 200:
                break
        return options

    # -------------------------------------------------------------- actions

    def _read(self, answers) -> Outcome | None:
        target = answers.pick("file") if "file" in answers else None
        if not target:
            return Outcome(False, "nothing to read")
        if target == self.open_path and "region" in answers:
            label = answers.pick("region")
            region = next((r for r in self.repo.regions(target) if r.label == label), None)
            if region:
                self.open_focus = (max(1, region.start - 20), region.end + 20)
                self.trace.detail("%s → %s" % (target, region.label))
                self.note("read %s, region %s" % (target, region.label))
                return None
        self.open_path = target
        total = len(self.repo.read(target).splitlines())
        self.open_focus = (1, min(total, OPEN_WINDOW))
        self.opened[target] = self.open_focus
        self.trace.detail("opened %s (%d lines)" % (target, total))
        self.trace.record("read", path=target, lines=total,
                          ranked=answers.ranked("file")[:4])
        self.note("opened %s (%d lines)" % (target, total))
        return None

    def _search(self, answers) -> Outcome | None:
        if "query" not in answers:
            return self._read(answers)
        term = answers.pick("query")
        self.searched.add(term)
        hits = locate.grep(self.repo.root, term)
        self.trace.detail("searched %r → %d files" % (term, len(hits)))
        self.trace.record("search", term=term, hits=hits[:10])
        self.note("searched %r, found: %s" % (term, ", ".join(hits[:8]) or "nothing"))
        if hits and self._shortlist is not None:
            for rel in hits:
                self._shortlist.setdefault(rel, "matched the search for %r" % term)
        return None

    def _edit(self, answers) -> Outcome | None:
        if not self.open_path:
            return self._read(answers)
        path = self.open_path
        region = None
        if "region" in answers:
            label = answers.pick("region")
            region = next((r for r in self.repo.regions(path) if r.label == label), None)
        if region is None:
            regions = self.repo.regions(path)
            region = regions[0] if regions else None
        if region is None:
            return Outcome(False, "no region to edit in %s" % path)
        region = self._widened(path, region)

        self.trace.detail("writing %s %s — %d candidates"
                          % (path, region.label, self.candidates))
        result = patch.write(self.one, self.writer, self.repo, path,
                             region.start, region.end, self.task, n=self.candidates,
                             related=self.related_code(path), failure=self.last_failure,
                             settle_at=self.settle_at, settle_grace=self.settle_grace)
        for c in result.candidates:
            self.trace.record("candidate", letter=c.letter, rejected=c.rejected,
                              p=c.probability, works=c.works, scope=c.scope,
                              seconds=c.seconds)
        if not result.chosen:
            self.trace.bad(result.reason)
            self.note("tried to write %s but every candidate failed the checks" % path)
            return None

        dropped = [c for c in result.candidates if c.rejected]
        if dropped:
            self.trace.detail("dropped %d: %s" % (
                len(dropped), "; ".join(sorted({c.rejected.split(":")[0] for c in dropped}))))
        self.trace.good("chose %s (p=%.2f, works=%.2f, out-of-scope=%.2f)"
                        % (result.chosen.letter, result.chosen.probability,
                           result.chosen.works, result.chosen.scope))

        if self.dry_run:
            edit = act.Edit(path, region.start, region.end, self.repo.read(path), "")
            self.trace.say(act.diff(self.repo.read(path),
                                    act.replace_region(self.repo.read(path), region.start,
                                                       region.end, result.chosen.code), path))
            self.note("would edit %s %s (dry run)" % (path, region.label))
            return Outcome(True, "dry run: proposed a change to %s" % path)

        if not self._permitted("edit", path, act.diff(
                self.repo.read(path),
                act.replace_region(self.repo.read(path), region.start, region.end,
                                   result.chosen.code), path)):
            self.trace.bad("the change to %s was declined" % path)
            self.note("the person declined the change to %s" % path)
            return None

        applied = self._apply_best(path, region, result)
        if applied is None:
            self.note("edited %s but nothing passed the check" % path)
        return None

    def _apply_best(self, path, region, result):
        """Apply the winner; if the project's own check rejects it, try the runner-up.

        The other candidates are already written and already judged, so falling
        back costs one test run and no model calls at all.
        """
        command = self._check_command()
        tried: set = set()
        candidate = result.chosen
        while candidate is not None:
            tried.add(candidate.letter)
            edit = act.apply_edit(self.repo, path, region.start, region.end, candidate.code)
            if not command:
                self.edits.append(edit)
                self.note("edited %s %s" % (path, region.label))
                self.trace.record("edit", path=path, region=region.label,
                                  letter=candidate.letter, checked=False)
                return edit
            run = act.run(command, self.repo.root)
            self.checks.append({"command": command, "ok": run.ok,
                                "output": run.output[-600:]})
            if not run.ok and self._environment_fault(run):
                # The runner itself is broken here — a missing tool, not a bad
                # patch. Keep the change and stop trusting this command.
                self.broken_commands.add(command)
                self.edits.append(edit)
                self.trace.bad("`%s` cannot run here; keeping the change unchecked" % command)
                self.trace.record("edit", path=path, region=region.label,
                                  letter=candidate.letter, checked=False,
                                  environment_fault=True)
                self.note("edited %s %s; `%s` could not run (environment)"
                          % (path, region.label, command))
                return edit
            if run.ok:
                self.edits.append(edit)
                self.last_failure = ()
                self.trace.good("%s passed with candidate %s" % (command, candidate.letter))
                self.trace.record("edit", path=path, region=region.label,
                                  letter=candidate.letter, checked=True, passed=True)
                self.note("edited %s %s; `%s` passed" % (path, region.label, command))
                return edit
            self.trace.bad("%s failed with candidate %s" % (command, candidate.letter))
            self.trace.record("edit", path=path, region=region.label,
                              letter=candidate.letter, checked=True, passed=False,
                              output=run.output[-300:])
            self.last_failure = (command, run.output)
            edit.undo(self.repo)
            candidate = patch.next_best(result, tried)
            if candidate is not None:
                self.trace.detail("falling back to candidate %s" % candidate.letter)
        self.failed_regions[(path, region.label)] = \
            self.failed_regions.get((path, region.label), 0) + 1
        self.note("no candidate passed `%s` in %s" % (command, path))
        return None

    def _widened(self, path: str, region):
        """A region that keeps failing is probably the wrong size.

        Some changes cannot be made in one place: a new argument has to appear
        in the function that takes it and in the one that passes it down, and a
        patch to either half alone leaves the tests red. Rewriting the same
        region again cannot fix that, so after a failure the window grows — to
        the neighbouring regions, and then to the whole file.
        """
        from .repo import Region
        tries = self.failed_regions.get((path, region.label), 0)
        if not tries:
            return region
        regions = self.repo.regions(path)
        total = len(self.repo.read(path).splitlines())
        if tries == 1 and len(regions) > 1:
            index = next((i for i, r in enumerate(regions) if r.label == region.label), 0)
            neighbours = regions[max(0, index - 1):index + 2]
            start, end = neighbours[0].start, neighbours[-1].end
            wider = Region("%s and its neighbours" % region.name, start, end, "block")
        else:
            wider = Region("the whole file", 1, total, "file")
        self.trace.detail("%s failed before; widening to %s" % (region.label, wider.label))
        return wider

    def _check_command(self) -> str:
        commands = self.repo.known_commands() if self.allow_commands else {}
        for name in commands:
            if "test" in name and name not in self.broken_commands:
                return name
        return ""

    def _environment_fault(self, run) -> bool:
        """Did the command fail because of the machine rather than the change?

        Asked in words instead of grepping for `ModuleNotFoundError`: the ways a
        toolchain can be missing are endless, and this is one Score question
        against output the agent already has in hand.
        """
        answers = self.one.ask({"output": run.output[-4000:], "task": self.task},
                               questions.read_result(run.output, self.task))
        self.trace.record("verdict", command=run.command, level=answers.value("verdict"),
                          our_fault=answers.p("our_fault"))
        return answers.value("verdict") < 0.5 and answers.p("our_fault") < 0.4

    def _create(self, answers) -> Outcome | None:
        """Make a file that does not exist yet: pick the path, then write it whole."""
        options = locate.new_file_options(self.repo, self.task)
        if not options:
            self.trace.detail("nowhere obvious to put a new file; reading instead")
            return self._read(answers)
        a = self.one.ask({"task": self.task,
                          "repository": {"name": os.path.basename(self.repo.root),
                                         "files": self.repo.files()[:150]}},
                         questions.pick_new_file(options, self.task))
        if a.p("needed") < questions.NEW_FILE:
            self.trace.detail("a new file is not what the task needs (%.2f)" % a.p("needed"))
            self.note("considered creating a file; the task does not call for one")
            return self._read(answers)
        path = a.pick("path")
        self.trace.detail("creating %s (p=%.2f)" % (path, a.probs("path").get(path, 0.0)))
        self.trace.record("create", path=path, needed=a.p("needed"),
                          ranked=a.ranked("path")[:4])

        result = patch.create(self.one, self.writer, self.repo, path, self.task,
                              n=max(3, self.candidates // 2),
                              related=self.related_code(path),
                              settle_at=self.settle_at, settle_grace=self.settle_grace)
        if not result.chosen:
            self.trace.bad(result.reason)
            self.note("tried to create %s but every candidate failed the checks" % path)
            return None
        body = result.chosen.code
        if self.dry_run:
            self.trace.say(act.diff("", body, path))
            self.note("would create %s (dry run)" % path)
            return Outcome(True, "dry run: proposed a new file %s" % path)
        if not self._permitted("create", path, act.diff("", body, path)):
            self.note("the person declined the new file %s" % path)
            return None
        edit = act.create_file(self.repo, path, body)
        edit.before = ""                  # so undo removes the file rather than blanking it
        self.edits.append(edit)
        self._shortlist = None
        self.repo._files = None
        self.open_path = path
        self.opened[path] = (1, OPEN_WINDOW)
        self.open_focus = (1, min(len(body.splitlines()), OPEN_WINDOW))
        self.trace.good("created %s (%d lines)" % (path, len(body.splitlines())))
        self.note("created %s (%d lines)" % (path, len(body.splitlines())))
        return None

    def _permitted(self, kind: str, key: str, preview: str = "") -> bool:
        if self.permit is None:
            return True
        return self.permit.allows(kind, key, preview)

    def _run(self, answers) -> Outcome | None:
        if "command" not in answers:
            return self._read(answers)
        command = answers.pick("command")
        verdict = gate.check(self.one, command, self.task)
        self.trace.record("gate", command=command, allowed=verdict.allowed,
                          reason=verdict.reason, scores=verdict.scores)
        if not verdict:
            self.trace.bad("refused `%s`: %s" % (command, verdict.reason))
            self.note("refused to run %r: %s" % (command, verdict.reason))
            return None
        if not self._permitted("run", command):
            self.trace.bad("declined: `%s`" % command)
            self.note("the person declined to run %r" % command)
            return None
        run = act.run(command, self.repo.root)
        self.checks.append({"command": command, "ok": run.ok, "output": run.output[-600:]})
        verdicts = self.one.ask({"output": run.output[-4000:], "task": self.task},
                                questions.read_result(run.output, self.task))
        meaning = verdicts.value("verdict")
        self.trace.detail("`%s` → exit %d, verdict %.1f/3" % (command, run.code, meaning))
        self.trace.record("run", command=command, code=run.code, verdict=meaning,
                          our_fault=verdicts.p("our_fault"), output=run.output[-400:])
        self.note("ran `%s`: exit %d, %s" % (
            command, run.code, "passed" if run.ok else run.output.strip().splitlines()[-1:][0]
            if run.output.strip() else "failed"))
        return None

    def _finish(self, answers) -> Outcome:
        if not self.edits:
            self.note("wanted to finish without changing anything")
            return Outcome(False, "stopped without making a change")
        return Outcome(True, "task carried out")


def build(task: str, root: str = ".", **kwargs) -> Agent:
    usage, writer_usage = Usage(), WriterUsage()
    one = SystemOne(usage=usage)
    writer = Writer(usage=writer_usage)
    return Agent(task, root, one=one, writer=writer, **kwargs)
