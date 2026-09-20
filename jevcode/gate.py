"""The gate in front of every shell command.

Deny lists are the usual answer: a regex for `rm -rf`, another for `git push
--force`, and a new one every time someone finds a way around them. They only
catch the shapes somebody thought of.

Here the question is asked in words, every time, about the actual command:
would this destroy work, is it unrelated to the task, does it reach outside the
repository. Three Nouls, a couple of hundred milliseconds, a fraction of a cent.
That is cheap enough to ask before every command rather than only the scary
looking ones — and unlike a regex it reads `find . -delete` the same way a
person does.

A short list of shapes is still refused outright, without asking. Not because
the model would get them wrong, but because no answer should be able to permit
them.
"""

from __future__ import annotations

import re

from . import questions

NEVER = [
    (re.compile(r"(^|[;&|\s])rm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", re.I), "recursive delete"),
    (re.compile(r"mkfs|dd\s+if=|:\(\)\{", re.I), "destroys the machine"),
    (re.compile(r"git\s+push\b.*(--force|-f)\b", re.I), "force push"),
    (re.compile(r"git\s+(reset\s+--hard|clean\s+-[a-z]*f)", re.I), "throws away uncommitted work"),
    (re.compile(r"(^|[;&|\s])(shutdown|reboot|halt)\b", re.I), "shuts the machine down"),
    (re.compile(r"curl[^|]*\|\s*(ba)?sh", re.I), "pipes the network into a shell"),
]


class Verdict:
    def __init__(self, allowed: bool, reason: str = "", scores: dict | None = None):
        self.allowed = allowed
        self.reason = reason
        self.scores = scores or {}

    def __bool__(self) -> bool:
        return self.allowed


def check(one, command: str, task: str) -> Verdict:
    """Refuse the unarguable shapes, then ask about the rest."""
    for pattern, why in NEVER:
        if pattern.search(command):
            return Verdict(False, "refused outright: %s" % why)
    a = one.ask({"command": command, "task": task},
                questions.guard_command(command, task))
    scores = {"destructive": a.p("destructive"), "off_task": a.p("off_task"),
              "outside": a.p("outside")}
    if scores["destructive"] >= questions.DANGEROUS:
        return Verdict(False, "may destroy work (%.2f)" % scores["destructive"], scores)
    if scores["off_task"] >= questions.OFF_TASK:
        return Verdict(False, "unrelated to the task (%.2f)" % scores["off_task"], scores)
    return Verdict(True, "", scores)
