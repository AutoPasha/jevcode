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

from . import act, gate, locate, patch, questions, tryout, verify
from .repo import Region, Repo
from .systemone import SystemOne, Usage
from .trace import Trace
from .writer import Writer, WriterUsage

MAX_HISTORY = 14
OPEN_WINDOW = 160
STUCK_LIMIT = 4        # edit rounds in a row that moved nothing, then stop
STUCK_WIDEN = 1        # ...and after this many, stop patching and write the file


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
        self.stuck = 0                  # edit rounds in a row that changed nothing
        self.verified = False           # the project's own check went red → green
        self.create_vetoed = False      # "a new file is not needed" already said once
        self._progress: verify.Result | None = None
        self._baseline_seconds = 0.0    # how long the project's own check takes
        self._preopen()

    # ---------------------------------------------------------------- state

    SOURCE_SUFFIX = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb",
                     ".java", ".php", ".c", ".cc", ".cpp", ".cs", ".swift", ".kt")

    def _preopen(self) -> None:
        """One source file and nothing else to change: open it without asking.

        A whole step — a request, a decision and a turn of the loop — used to go
        on establishing that the only file in the repository is the file to
        change. That is not judgement, it is counting, and counting belongs in
        the code. Anything bigger than a single-file exercise still goes through
        the usual choice.
        """
        try:
            files = self.repo.files()
        except OSError:
            return
        sources = [f for f in files
                   if f.endswith(self.SOURCE_SUFFIX) and not self._looks_like_test(f)]
        if len(sources) != 1:
            return
        path = sources[0]
        self.open_path = path
        total = len(self.repo.read(path).splitlines())
        self.open_focus = (1, min(total or 1, OPEN_WINDOW))
        self.opened[path] = self.open_focus
        self.note("opened %s (%d lines) — the only source file here" % (path, total))

    @staticmethod
    def _looks_like_test(rel: str) -> bool:
        name = os.path.basename(rel).lower()
        parts = rel.lower().replace("\\", "/").split("/")
        return ("test" in name or "spec" in name
                or any(p in ("test", "tests", "spec", "specs", "__tests__") for p in parts))

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
        if self.stuck >= STUCK_LIMIT:
            # Four full rounds of candidates, none of which moved a single test.
            # The remaining steps will not either, and the whole cost of this
            # agent is in the drafts they would burn.
            self.trace.record("stop", why="no progress", rounds=self.stuck)
            return Outcome(False, "%d rounds of edits in a row moved nothing"
                                  % self.stuck)
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
        region, whole = self._scoped(path, region)

        self.trace.detail("writing %s %s — %d candidates"
                          % (path, region.label, self.candidates))
        result = patch.draft(self.one, self.writer, self.repo, path,
                             region.start, region.end, self.task, n=self.candidates,
                             related=self.related_code(path), failure=self.last_failure,
                             settle_at=self.settle_at, settle_grace=self.settle_grace,
                             whole=whole, label=region.name)
        raced = self._race(path, region, result)
        if raced is None and result.chosen is None:
            patch.judge(self.one, self.repo, path, region.start, region.end,
                        self.task, result)
        for c in result.candidates:
            self.trace.record("candidate", letter=c.letter, rejected=c.rejected,
                              p=c.probability, works=c.works, scope=c.scope,
                              seconds=c.seconds)
        if not result.chosen:
            self.trace.bad(result.reason)
            if raced is None:
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

        applied = self._apply_best(path, region, result, raced)
        if self.verified:
            # The project's own command was red before this edit and is green
            # after it. That is the answer to "is it done", and it is a fact —
            # no point spending a request asking for an opinion about it.
            self.trace.record("stop", why="the project's own check passed")
            return Outcome(True, "task carried out and checked")
        if applied is None:
            self.note("edited %s but nothing passed the check" % path)
        return None

    def _race(self, path: str, region, result) -> dict | None:
        """Try every candidate against the project's own tests, all at once.

        This is the step that used to be a chain: ask Jev which draft looks
        best, apply it, run the suite, undo, apply the next one, run the suite
        again. Six candidates meant up to six runs one after another plus a
        request spent ordering them, and the order could simply be wrong.

        Running them side by side in throwaway copies costs one run's worth of
        waiting and answers with a fact. The request is only spent when the
        tests genuinely cannot separate two drafts, which is rare.
        """
        command = self._check_command()
        if not command or self.dry_run or len(result.alive) < 2:
            return None
        if not tryout.affordable(self.repo.root):
            self.trace.detail("too big a tree to try the candidates side by side")
            return None
        before = self.baseline(command)
        trials = tryout.race(self.repo, command, path, region.start, region.end,
                             result.alive, timeout=self._patience())
        if not trials:
            return None
        self.trace.detail("tried %d candidates against `%s` at once: %s"
                          % (len(trials), command,
                             ", ".join("%s %s" % (t.letter, verify.describe(t.result))
                                       for t in sorted(trials.values(),
                                                       key=lambda t: t.letter))))
        order = []
        if tryout.undecided(trials, before):
            # Every draft that moved anything moved it by exactly the same
            # amount. That is the one case the tests cannot settle, so it is
            # the one case worth a request.
            patch.judge(self.one, self.repo, path, region.start, region.end,
                        self.task, result)
            order = [c.letter for c in sorted(result.alive, key=lambda c: -c.probability)]
        winner = tryout.pick(trials, before, order)
        for trial in trials.values():
            self.trace.record("trial", letter=trial.letter, passed=trial.run.ok,
                              passed_tests=trial.result.passed,
                              failed_tests=trial.result.failed,
                              seconds=trial.run.seconds, chosen=trial.letter == winner)
        if not winner:
            worst = sorted(trials.values(), key=lambda t: t.letter)[0]
            self.last_failure = (command, worst.run.output)
            self.checks.append({"command": command, "ok": False,
                                "output": worst.run.output[-600:]})
            self.failed_regions[(path, region.label)] = \
                self.failed_regions.get((path, region.label), 0) + 1
            self.stuck += 1
            result.chosen = None
            result.reason = ("none of %d candidates moved `%s` forward"
                             % (len(trials), command))
            self.note("wrote %d candidates for %s %s; none of them moved `%s`"
                      % (len(trials), path, region.label, command))
            return trials
        result.chosen = next(c for c in result.alive if c.letter == winner)
        result.reason = "the tests picked %s out of %d" % (winner, len(trials))
        return trials

    def _patience(self) -> int:
        """How long a candidate's test run may take before it is a hang.

        The suite itself is the measure: it was just run once on this machine,
        and a candidate that takes ten times that long is not slow, it is a
        loop that never ends — which a writer produces often enough to matter
        when six of them run at once. Without this the whole step waits out the
        transport's own ceiling for something that will never finish.
        """
        if not self._baseline_seconds:
            return 300
        return int(max(20, min(300, self._baseline_seconds * 10)))

    def baseline(self, command: str) -> verify.Result:
        """Where the project's own check stood before the agent touched anything.

        Without it a red suite is just red, and every partial change looks
        identical to every wrong one. With it, `4 failed, 1 passed` after
        `5 failed, 0 passed` is visibly progress and gets to survive.
        """
        if self._progress is None:
            if not command:
                self._progress = verify.Result()
            else:
                run = act.run(command, self.repo.root)
                self._progress = verify.parse(run.output, run.code)
                self._baseline_seconds = run.seconds
                self.trace.detail("before the change: %s" % verify.describe(self._progress))
        return self._progress

    def _apply_best(self, path, region, result, trials: dict | None = None):
        """Apply the winner; if the project's own check rejects it, try the runner-up.

        The other candidates are already written and already judged, so falling
        back costs one test run and no model calls at all.

        A candidate is only thrown away when it fails to move anything. Half of
        a change that needs two places will leave the suite red and still be
        worth keeping — that is what `verify.better` is for, and keeping it is
        the difference between finishing a task in three steps and rewriting the
        same region until the budget runs out.
        """
        command = self._check_command()
        before = self.baseline(command)
        if trials and result.chosen and result.chosen.letter in trials:
            # The race already ran this exact code against this exact command.
            # Running it again would cost another suite and tell us the same
            # thing, so the trial is the check.
            return self._keep(path, region, result.chosen, command, before,
                              trials[result.chosen.letter].run)
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
            after = verify.parse(run.output, run.code)
            self.checks.append({"command": command, "ok": run.ok,
                                "output": run.output[-600:]})
            if not run.ok and self._environment_fault(run, after):
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
                self._progress = after
                self.stuck = 0
                # Green now, red before: the project itself says the task is
                # done. Green before as well means this suite never measured
                # the task, so it proves nothing and the loop carries on.
                self.verified = not before.ok
                self.trace.good("%s passed with candidate %s" % (command, candidate.letter))
                self.trace.record("edit", path=path, region=region.label,
                                  letter=candidate.letter, checked=True, passed=True,
                                  verified=self.verified)
                self.note("edited %s %s; `%s` passed" % (path, region.label, command))
                return edit
            if verify.better(after, before):
                # Still red, but fewer things are wrong than before. This is
                # half of a change that needs two places, and throwing it away
                # would put the next attempt back where this one started.
                self.edits.append(edit)
                self._progress = after
                self.stuck = 0
                self.last_failure = (command, run.output)
                self.trace.good("kept candidate %s: %s (was %s)"
                                % (candidate.letter, verify.describe(after),
                                   verify.describe(before)))
                self.trace.record("edit", path=path, region=region.label,
                                  letter=candidate.letter, checked=True, passed=False,
                                  progress=True, passed_tests=after.passed,
                                  failed_tests=after.failed,
                                  was_passing=before.passed, output=run.output[-300:])
                self.note("edited %s %s; `%s` still fails but %s (was %s)"
                          % (path, region.label, command,
                             verify.describe(after), verify.describe(before)))
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
        self.stuck += 1
        self.note("no candidate passed `%s` in %s" % (command, path))
        return None

    def _keep(self, path, region, candidate, command: str, before, run):
        """Apply a candidate whose test run has already happened, in a copy.

        Everything the serial path learns from running the suite is already in
        `run`: the exit code, the counts, whether the runner itself is broken.
        So this writes the file and records what is known, instead of spending
        another suite proving it twice.
        """
        after = verify.parse(run.output, run.code)
        edit = act.apply_edit(self.repo, path, region.start, region.end, candidate.code)
        self.checks.append({"command": command, "ok": run.ok,
                            "output": run.output[-600:]})
        self.edits.append(edit)
        if not run.ok and self._environment_fault(run, after):
            self.broken_commands.add(command)
            self.trace.bad("`%s` cannot run here; keeping the change unchecked" % command)
            self.trace.record("edit", path=path, region=region.label,
                              letter=candidate.letter, checked=False,
                              environment_fault=True)
            self.note("edited %s %s; `%s` could not run (environment)"
                      % (path, region.label, command))
            return edit
        self._progress = after
        self.stuck = 0
        if run.ok:
            self.last_failure = ()
            self.verified = not before.ok
            self.trace.good("%s passed with candidate %s" % (command, candidate.letter))
            self.trace.record("edit", path=path, region=region.label,
                              letter=candidate.letter, checked=True, passed=True,
                              verified=self.verified)
            self.note("edited %s %s; `%s` passed" % (path, region.label, command))
            return edit
        self.last_failure = (command, run.output)
        self.trace.good("kept candidate %s: %s (was %s)"
                        % (candidate.letter, verify.describe(after), verify.describe(before)))
        self.trace.record("edit", path=path, region=region.label,
                          letter=candidate.letter, checked=True, passed=False,
                          progress=True, passed_tests=after.passed,
                          failed_tests=after.failed, was_passing=before.passed,
                          output=run.output[-300:])
        self.note("edited %s %s; `%s` still fails but %s (was %s)"
                  % (path, region.label, command, verify.describe(after),
                     verify.describe(before)))
        return edit

    def _scoped(self, path: str, region) -> tuple:
        """How much of the file this edit gets to rewrite.

        A file whose functions are all signatures over `pass` is not something
        to patch region by region: no single region can make the tests pass, so
        every correct half gets rejected by the suite in turn. That shape is
        visible in the code before anything is written, so the agent writes the
        whole file at once instead of discovering it the expensive way.
        """
        total = max(len(self.repo.read(path).splitlines()), 1)
        whole = Region("the whole file", 1, total, "file")
        if self.repo.greenfield(path):
            stubs = self.repo.stubs(path)
            self.trace.detail("%s is a skeleton (%d unwritten %s) — writing it whole"
                              % (path, len(stubs),
                                 "body" if len(stubs) == 1 else "bodies"))
            self.trace.record("scope", path=path, whole=True, stubs=len(stubs))
            return whole, True
        if self.stuck > STUCK_WIDEN:
            self.trace.detail("%d rounds moved nothing — writing %s whole"
                              % (self.stuck, path))
            self.trace.record("scope", path=path, whole=True, stuck=self.stuck)
            return whole, True
        wider = self._widened(path, region)
        if any(s.start == wider.start and s.end == wider.end
               for s in self.repo.stubs(path)):
            # The region itself is a blank — an empty class in a file that is
            # otherwise written. Patching it would be sending a writer to edit
            # `pass`; what it needs is the brief and the room to author the
            # thing outright, scoped to that class and nothing else.
            self.trace.detail("%s is a blank — writing it out in full" % wider.label)
            self.trace.record("scope", path=path, whole=True, stub=wider.label)
            return wider, True
        return wider, False

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

    def _environment_fault(self, run, result=None) -> bool:
        """Did the command fail because of the machine rather than the change?

        Asked in words instead of grepping for `ModuleNotFoundError`: the ways a
        toolchain can be missing are endless, and this is one Score question
        against output the agent already has in hand.

        Asked only when there is something to ask about. A runner that reported
        `3 failed, 2 passed` plainly ran, so the machine is fine and the
        question has one possible answer — and it used to be asked once per
        rejected candidate, which is six requests a step for nothing.
        """
        if result is not None:
            if result.readable:
                return False
            if result.missing:
                return True
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
        # Two questions about one decision, and they can disagree forever: the
        # step already chose `create` at p=0.87 while this one answers 0.49 and
        # sends the agent back to reading, twenty-four times in a row on
        # slugify. So the veto only stands while the step itself is unsure, and
        # it never stands twice — after that the choice made out there wins and
        # this question is left with the job it is actually good at, the path.
        chosen = answers.probs("action").get("create", 0.0) if "action" in answers else 0.0
        if a.p("needed") < questions.NEW_FILE and chosen < 0.6 and not self.create_vetoed:
            self.create_vetoed = True
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
        result = verify.parse(run.output, run.code)
        if command == self._check_command():
            was = self.baseline(command)
            self._progress = result
            if run.ok and self.edits and was.readable and not was.ok:
                self.verified = True
        if result.readable:
            # The runner counted the tests itself. Asking a model what its own
            # output means would be a request spent re-reading a number.
            meaning = 3.0 if run.ok else 1.0
            self.trace.detail("`%s` → exit %d, %s"
                              % (command, run.code, verify.describe(result)))
            self.trace.record("run", command=command, code=run.code, verdict=meaning,
                              passed_tests=result.passed, failed_tests=result.failed,
                              output=run.output[-400:])
        else:
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
