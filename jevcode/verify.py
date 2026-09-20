"""Reading a test run the way a person reads one: did it get better?

The agent used to have exactly one bit of information about a change — the
command's exit code — and one response to a red one: undo. That is wrong for
any task whose test cannot pass until several places are written. Filling in
the first of two empty functions leaves the suite red, so the work was thrown
away, and the same region was written again from the same starting point until
the step budget ran out. The agent was not slow at hard tasks; it was erasing
its own progress.

So a run is parsed into counts, and counts are compared. `4 failed, 1 passed`
after `5 failed, 0 passed` is progress and is kept, even though the exit code
is identical. None of this is an opinion, which matters twice: it is free, and
it cannot be talked out of the truth by a confident-sounding draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# pytest: "3 failed, 2 passed, 1 error in 0.42s"
PYTEST_COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")
PYTEST_FAILED_NAME = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
# unittest: "Ran 7 tests in 0.001s" + "FAILED (failures=3, errors=1)" / "OK"
UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
UNITTEST_BAD = re.compile(r"^(?:FAILED|OK)(?:\s*\((.*)\))?\s*$", re.M)
UNITTEST_KIND = re.compile(r"(failures|errors|unexpected successes)=(\d+)")
# go: "--- FAIL: TestName" / "--- PASS: TestName"
GO_LINE = re.compile(r"^\s*--- (PASS|FAIL): (\S+)", re.M)
# cargo: "test result: FAILED. 3 passed; 2 failed;"
CARGO = re.compile(r"test result:.*?(\d+) passed;\s*(\d+) failed", re.S)
# jest / vitest: "Tests:       2 failed, 3 passed, 5 total"
JEST = re.compile(r"^Tests?:\s+(.*?)$", re.M)
# TAP, which is what `node --test` speaks: a summary of "# pass 1" lines, and
# one "not ok 2 - name" per failure.
TAP_COUNT = re.compile(r"^# (tests|pass|fail|skipped|todo|cancelled)\s+(\d+)\s*$", re.M)
TAP_NOT_OK = re.compile(r"^not ok \d+ - (.+?)\s*$", re.M)

# The runner never started: a missing interpreter, an unimportable module at
# collection time, a toolchain that is not on this machine. Different from a
# failing test and handled differently — the change is not what is wrong.
MISSING = (
    "command not found", "not found", "no such file or directory",
    "is not recognized as an internal", "permission denied",
)
BROKEN = MISSING + (
    "modulenotfounderror", "importerror", "cannot find module",
    "no tests ran", "error: could not compile", "collected 0 items",
    "no module named",
)


@dataclass
class Result:
    """What one run of the project's own command said."""
    ok: bool = False
    passed: int = 0
    failed: int = 0
    total: int = 0
    failing: set = field(default_factory=set)
    readable: bool = False      # the runner reported counts, so it did run
    broken: bool = False        # nothing ran, for one reason or another
    missing: bool = False       # the command itself is not on this machine

    @property
    def score(self) -> tuple:
        """Ordering key: passing tests first, then fewer failures."""
        return (self.passed, -self.failed)


def parse(output: str, code: int = 1) -> Result:
    text = output or ""
    low = text.lower()
    res = Result(ok=(code == 0))

    counts = {kind.rstrip("s"): 0 for kind in ("passed", "failed", "error", "skipped")}
    seen = False
    for n, kind in PYTEST_COUNT.findall(text):
        key = kind.rstrip("s").replace("error", "error")
        if key in ("passed", "failed", "error", "skipped"):
            counts[key] = max(counts[key], int(n))
            seen = True
    if seen:
        res.passed, res.failed = counts["passed"], counts["failed"] + counts["error"]
        res.total = res.passed + res.failed + counts["skipped"]
        res.failing = set(PYTEST_FAILED_NAME.findall(text))
        res.readable = res.total > 0

    if not res.readable:
        ran = UNITTEST_RAN.search(text)
        verdict = UNITTEST_BAD.search(text)
        if ran and verdict:
            res.total = int(ran.group(1))
            bad = sum(int(n) for _, n in UNITTEST_KIND.findall(verdict.group(1) or ""))
            res.failed = bad
            res.passed = max(res.total - bad, 0)
            res.readable = res.total > 0

    if not res.readable:
        lines = GO_LINE.findall(text)
        if lines:
            res.passed = sum(1 for verdict, _ in lines if verdict == "PASS")
            res.failing = {name for verdict, name in lines if verdict == "FAIL"}
            res.failed = len(res.failing)
            res.total = len(lines)
            res.readable = True

    if not res.readable:
        cargo = CARGO.search(text)
        if cargo:
            res.passed, res.failed = int(cargo.group(1)), int(cargo.group(2))
            res.total = res.passed + res.failed
            res.readable = res.total > 0

    if not res.readable:
        jest = JEST.search(text)
        if jest:
            for n, kind in re.findall(r"(\d+)\s+(passed|failed|total)", jest.group(1)):
                if kind == "passed":
                    res.passed = int(n)
                elif kind == "failed":
                    res.failed = int(n)
                elif kind == "total":
                    res.total = int(n)
            res.readable = res.total > 0

    if not res.readable:
        # `node --test` ships with node and needs no package.json, so a
        # JavaScript task in this benchmark uses it. Without this branch its
        # output parses to nothing, every change looks like no change at all,
        # and the loop gives up on a task it was in the middle of solving.
        tap = {kind: int(n) for kind, n in TAP_COUNT.findall(text)}
        if tap.get("tests"):
            res.passed, res.failed = tap.get("pass", 0), tap.get("fail", 0)
            res.total = tap["tests"]
            res.failing = set(TAP_NOT_OK.findall(text))
            res.readable = True

    if not res.readable:
        res.broken = any(mark in low for mark in BROKEN)
        # "pytest: not found" is not a verdict on the change and never needs a
        # model to interpret it: the command is simply not on this machine.
        res.missing = any(mark in low for mark in MISSING)
    return res


def better(after: Result, before: Result) -> bool:
    """Did the change move the suite forward, even though it is still red?

    Deliberately strict. "More tests pass" is progress. "Fewer failures" alone
    is not, unless the suite is the same size — a patch that breaks collection
    reports one error instead of nine failures and would otherwise look like a
    triumph.
    """
    if after.ok and not before.ok:
        return True
    if not after.readable or not before.readable:
        return False
    if after.passed > before.passed:
        return True
    if after.total < before.total:
        return False
    if after.passed == before.passed and after.failed < before.failed:
        return True
    if (after.failing and before.failing and after.failing < before.failing
            and after.passed >= before.passed):
        return True
    return False


def worse(after: Result, before: Result) -> bool:
    """Did it go backwards? Used to prefer the old code over a bad candidate."""
    if not after.readable or not before.readable:
        return False
    return after.passed < before.passed or after.total < before.total


def describe(res: Result) -> str:
    if not res.readable:
        return "the runner did not report any tests"
    return "%d passed, %d failed" % (res.passed, res.failed)
