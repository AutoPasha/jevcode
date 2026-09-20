"""Asking before doing, and remembering the answer.

The gate in `gate.py` decides whether a command is *safe*. This is a different
question: whether the person sitting at the terminal wants it to happen. One is
judged by the model, the other can only be answered by the human, and folding
them together is how agents end up either nagging about `ls` or quietly running
`git push`.

Answers are remembered at the granularity people actually think in: this exact
command, or every edit in this session. Nothing is remembered across sessions —
a permission that outlives the conversation it was given in is a trap.
"""

from __future__ import annotations

ONCE, ALWAYS, NO = "once", "always", "no"


class Permission:
    def __init__(self, policy: str = "ask", ui=None):
        self.policy = policy            # ask | allow | deny
        self.ui = ui
        self.remembered: set = set()    # keys the person said "always" to

    def allows(self, kind: str, key: str, preview: str = "") -> bool:
        """`kind` is what is about to happen, `key` is the thing to remember."""
        if self.policy == "allow":
            return True
        if self.policy == "deny":
            return False
        if kind in self.remembered or (kind + ":" + key) in self.remembered:
            return True
        if self.ui is None:
            return True
        if preview:
            self.ui.say()
            self.ui.diff(preview) if preview.startswith(("---", "+++", "@@")) \
                else self.ui.say(preview)
        answer = self.ui.ask(
            _QUESTION.get(kind, "Allow this?") % key,
            ["yes, once", "yes, and stop asking about %s" % _SCOPE.get(kind, kind),
             "no"], default=0)
        if answer == 0:
            return True
        if answer == 1:
            self.remembered.add(kind)
            return True
        return False

    def allow_everything(self) -> None:
        self.policy = "allow"


_QUESTION = {
    "run": "Run `%s`?",
    "edit": "Apply this change to %s?",
    "create": "Create %s?",
}

_SCOPE = {
    "run": "commands this session",
    "edit": "edits this session",
    "create": "new files this session",
}
