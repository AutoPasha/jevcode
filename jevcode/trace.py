"""What the agent shows while it works, and what it writes down afterwards.

Every decision this agent makes is a number, so the log is not a story about
what the model was thinking — it is the distribution it actually returned. When
it goes wrong you can see which question was answered badly, and fix that
question. That is a different debugging experience from reading a paragraph of
invented reasoning.
"""

from __future__ import annotations

import json
import os
import sys
import time

COLORS = {
    "dim": "\033[2m", "bold": "\033[1m", "red": "\033[31m", "green": "\033[32m",
    "yellow": "\033[33m", "blue": "\033[34m", "cyan": "\033[36m", "off": "\033[0m",
}


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


class Trace:
    def __init__(self, path: str | None = None, quiet: bool = False, color: bool | None = None):
        self.path = path
        self.quiet = quiet
        self.color = _supports_color() if color is None else color
        self.started = time.time()
        self.records: list = []
        if path:
            open(path, "w").close()

    # ------------------------------------------------------------- printing

    def paint(self, text: str, name: str) -> str:
        if not self.color:
            return text
        return COLORS[name] + text + COLORS["off"]

    def say(self, text: str = "") -> None:
        if not self.quiet:
            print(text, flush=True)

    def step(self, number: int, action: str, probability: float, confidence: float) -> None:
        bar = self.paint("%-7s" % action, "bold")
        self.say("%s %s %s" % (
            self.paint("step %d" % number, "dim"), bar,
            self.paint("p=%.2f conf=%.2f" % (probability, confidence), "dim")))

    def detail(self, text: str) -> None:
        self.say("        " + self.paint(text, "dim"))

    def good(self, text: str) -> None:
        self.say("        " + self.paint(text, "green"))

    def bad(self, text: str) -> None:
        self.say("        " + self.paint(text, "red"))

    def options(self, ranked: list, limit: int = 3) -> None:
        for name, p in ranked[:limit]:
            self.say("          %s %s" % (self.paint("%.2f" % p, "cyan"), name))

    # -------------------------------------------------------------- writing

    def record(self, kind: str, **fields) -> None:
        row = {"t": round(time.time() - self.started, 2), "kind": kind}
        row.update(fields)
        self.records.append(row)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Jev is charged per input token; output tokens are free.
    # https://docs.typesafe.ai/models
    LIST_PRICE_PER_MTOK = 0.042

    def summary(self, one_usage, writer_usage, outcome: str) -> None:
        if one_usage.cost or writer_usage.cost:
            money = " · %.4f %s" % (one_usage.cost + writer_usage.cost,
                                    one_usage.currency or writer_usage.currency or "")
        else:
            money = " · ~$%.4f of thinking" % (
                one_usage.input_tokens / 1e6 * self.LIST_PRICE_PER_MTOK)
        self.say()
        self.say(self.paint(outcome, "bold"))
        self.say(self.paint(
            "%d decisions in %d requests (%.1fs, %d tokens in) · %d writer calls (%.1fs)%s"
            " · %.1fs total"
            % (one_usage.questions, one_usage.requests, one_usage.seconds,
               one_usage.input_tokens, writer_usage.calls, writer_usage.seconds, money,
               time.time() - self.started), "dim"))
