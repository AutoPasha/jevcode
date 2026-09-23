"""Trying every candidate at once, against the project's own tests.

The agent writes several candidates and has to pick one. Asking a model which
is best is an opinion; running the project's own check on it is a fact, and the
facts are already lying there — the candidates are written, the suite takes a
second, and the machine has more than one core.

So all of them are run at the same time, each in its own throwaway copy of the
repository. What that replaces is a chain: judge the pile, apply the favourite,
run the tests, undo, apply the runner-up, run the tests again — up to six runs
end to end plus a request spent ordering them. Here it is one run's worth of
waiting for all six, no request at all when one of them turns the suite green,
and the answer is the truth rather than a ranking that can be wrong.

Nothing here writes to the repository the person is working in.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass

from . import act, verify

SKIP = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "target",
        "dist", "build", ".next", ".cache"}

MAX_FILES = 3000
MAX_BYTES = 60 * 1024 * 1024
WORKERS = 4


@dataclass
class Trial:
    """One candidate, and what the project's own command said about it."""
    letter: str
    run: act.Run
    result: verify.Result

    @property
    def ok(self) -> bool:
        return self.run.ok


def affordable(root: str) -> bool:
    """Is copying this repository per candidate cheap enough to be worth it?

    A few exercise files, yes. A checkout with a build tree in it, no — there
    the copying would cost more than the sequential runs it saves, so the caller
    falls back to applying one candidate at a time.
    """
    files = total = 0
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(here, name))
            except OSError:
                pass
            if files > MAX_FILES or total > MAX_BYTES:
                return False
    return True


def race(repo, command: str, rel: str, start: int, end: int, candidates: list,
         timeout: int = 300, workers: int = WORKERS) -> dict:
    """Run `command` once per candidate, in parallel, in copies of the repo.

    Returns the trials by letter. The repository itself is untouched: the
    winner is applied by the caller, once, knowing what it does.
    """
    if not candidates:
        return {}
    before = repo.read(rel)
    scratch = tempfile.mkdtemp(prefix="jevcode-race-")
    try:
        prepared = []
        for candidate in candidates:
            root = os.path.join(scratch, candidate.letter)
            shutil.copytree(repo.root, root, symlinks=True,
                            ignore=shutil.ignore_patterns(*SKIP))
            with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                fh.write(act.replace_region(before, start, end, candidate.code))
            prepared.append((candidate, root))
        trials = {}
        with cf.ThreadPoolExecutor(max(1, min(len(prepared), workers))) as pool:
            futures = {pool.submit(act.run, command, root, timeout): candidate
                       for candidate, root in prepared}
            for future in cf.as_completed(futures):
                candidate = futures[future]
                run = future.result()
                trials[candidate.letter] = Trial(candidate.letter, run,
                                                 verify.parse(run.output, run.code))
        return trials
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def pipeline(repo, command: str, rel: str, start: int, end: int, source,
             timeout: int = 300, workers: int = WORKERS, stop=None) -> tuple:
    """Test each candidate the moment it is written, and stop at the first green one.

    `race` needs the whole pile before it can start, so the slowest draft sets
    the pace of the step even though nothing about it is better — with a writer
    that thinks before it answers, that straggler is most of the wait. Here the
    drafts arrive one at a time from `source` and each goes under the project's
    own command straight away, while the rest are still being written.

    The first candidate whose run comes back green ends the step: the remaining
    runs are killed, `source` is told to stop, and nothing waits for a draft
    whose verdict cannot change the answer. The runs that did finish are
    returned either way — when nothing goes green they are exactly what `pick`
    needs to choose the candidate that moved the suite furthest.

    Returns (trials by letter, winning letter or "", candidates seen in order).
    """
    before_text = repo.read(rel)
    scratch = tempfile.mkdtemp(prefix="jevcode-pipe-")
    # One event for the whole step. The caller passes its own when the source is
    # a writer that should also be told to stop: the drafts still in flight are
    # paid for either way, but there is no sense waiting for them.
    stop = stop if stop is not None else threading.Event()
    events: queue.Queue = queue.Queue()
    seen: list = []
    trials: dict = {}
    winner = ""
    started: list = []
    pool = cf.ThreadPoolExecutor(max(1, workers))

    def feed() -> None:
        try:
            for candidate in source:
                if stop.is_set():
                    break
                events.put(("draft", candidate))
        except Exception as ex:                          # noqa: BLE001 - reported, not raised
            events.put(("failed", ex))
        finally:
            events.put(("end", None))

    def attempt(candidate) -> act.Run:
        root = os.path.join(scratch, candidate.letter)
        shutil.copytree(repo.root, root, symlinks=True,
                        ignore=shutil.ignore_patterns(*SKIP))
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(act.replace_region(before_text, start, end, candidate.code))
        return act.run(command, root, timeout, stop=stop)

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    try:
        writing = True
        running = 0
        while (writing or running) and not winner:
            kind, payload = events.get()
            if kind == "draft":
                seen.append(payload)
                running += 1
                future = pool.submit(attempt, payload)
                started.append(future)
                future.add_done_callback(
                    lambda f, c=payload: events.put(("trial", (c, f))))
            elif kind == "trial":
                running -= 1
                candidate, future = payload
                try:
                    run = future.result()
                except Exception as ex:                  # noqa: BLE001 - reported, not raised
                    run = act.Run(command, 127, str(ex)[:300], 0.0)
                if run.abandoned:
                    continue
                trials[candidate.letter] = Trial(candidate.letter, run,
                                                 verify.parse(run.output, run.code))
                if run.ok:
                    winner = candidate.letter
            else:                                        # "end" or "failed"
                writing = False
        return trials, winner, seen
    finally:
        # Set first, then wait: the runs still going are watching this event and
        # die within a tick of it, and the scratch tree must outlive them or the
        # copies vanish from under a process that is still reading them.
        stop.set()
        cf.wait(started, timeout=10)
        pool.shutdown(wait=False)
        feeder.join(timeout=2)
        shutil.rmtree(scratch, ignore_errors=True)


def pick(trials: dict, before: verify.Result, order: list | None = None) -> str:
    """The winner, by what the tests said and nothing else.

    Green wins. Failing that, the candidate that moved the suite furthest
    forward wins, and `order` — Jev's ranking, if the caller bothered to ask for
    one — only breaks ties between candidates the tests cannot tell apart.
    """
    rank = {letter: i for i, letter in enumerate(order or [])}

    def key(trial: Trial) -> tuple:
        return (0 if trial.ok else 1,
                -trial.result.passed,
                trial.result.failed,
                rank.get(trial.letter, 99),
                trial.letter)

    green = [t for t in trials.values() if t.ok]
    if green:
        return min(green, key=key).letter
    moved = [t for t in trials.values() if verify.better(t.result, before)]
    if moved:
        return min(moved, key=key).letter
    return ""


def undecided(trials: dict, before: verify.Result) -> bool:
    """Do the tests actually separate these candidates, or is it a coin flip?

    Two candidates that both turn the suite green are both right and either will
    do. Two that both leave it exactly where it was are indistinguishable, and
    that is the one case worth spending a request on.
    """
    if any(t.ok for t in trials.values()):
        return False
    moved = [t for t in trials.values() if verify.better(t.result, before)]
    if len(moved) < 2:
        return False
    return len({(t.result.passed, t.result.failed) for t in moved}) == 1
