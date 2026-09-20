"""The terminal side: colour, a spinner, line editing, diffs, prompts.

Deliberately stdlib only. A full-screen TUI would need a framework and a build
step, and it would buy very little here: this agent's output is a stream of
decisions, which reads better as scrollback you can copy out of than as a pane
that repaints. What is worth the effort is the part people actually touch —
line editing, history, completion of `@files` and `/commands`, and a prompt
that does not lie about what is happening.
"""

from __future__ import annotations

import itertools
import os
import shutil
import sys
import threading
import time

from .config import data_dir

COLORS = {
    "dim": "\033[2m", "bold": "\033[1m", "red": "\033[31m", "green": "\033[32m",
    "yellow": "\033[33m", "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
    "grey": "\033[90m", "off": "\033[0m",
}

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("JEVCODE_FORCE_COLOR") == "1":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class Ui:
    def __init__(self, color: bool | None = None, quiet: bool = False):
        self.color = supports_color() if color is None else color
        self.quiet = quiet
        self._spinner: Spinner | None = None

    # -------------------------------------------------------------- output

    def paint(self, text: str, *names: str) -> str:
        if not self.color or not names:
            return text
        return "".join(COLORS[n] for n in names) + text + COLORS["off"]

    def say(self, text: str = "") -> None:
        if self.quiet:
            return
        if self._spinner:
            self._spinner.clear()
        print(text, flush=True)

    def step(self, action: str, detail: str, probability: float = 0.0) -> None:
        mark = {"read": "○", "search": "◍", "edit": "◆", "create": "✦",
                "run": "▸", "finish": "✔"}.get(action, "·")
        tail = self.paint(" p=%.2f" % probability, "grey") if probability else ""
        self.say("%s %s %s%s" % (self.paint(mark, "cyan"),
                                 self.paint("%-6s" % action, "bold"), detail, tail))

    def detail(self, text: str) -> None:
        self.say("  " + self.paint(text, "grey"))

    def good(self, text: str) -> None:
        self.say("  " + self.paint(text, "green"))

    def bad(self, text: str) -> None:
        self.say("  " + self.paint(text, "red"))

    def warn(self, text: str) -> None:
        self.say(self.paint("! ", "yellow") + text)

    def rule(self, label: str = "") -> None:
        width = shutil.get_terminal_size((80, 24)).columns
        line = "─" * max(4, width - len(label) - 2)
        self.say(self.paint(("%s %s" % (label, line)).strip(), "grey"))

    def diff(self, text: str) -> None:
        for line in text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                self.say(self.paint(line, "bold"))
            elif line.startswith("@@"):
                self.say(self.paint(line, "cyan"))
            elif line.startswith("+"):
                self.say(self.paint(line, "green"))
            elif line.startswith("-"):
                self.say(self.paint(line, "red"))
            else:
                self.say(self.paint(line, "grey"))

    # ------------------------------------------------------------- spinner

    def working(self, label: str = "thinking") -> "Spinner":
        if self.quiet or not self.color:
            return _Silent()
        self._spinner = Spinner(label)
        self._spinner.start()
        return self._spinner

    def done(self) -> None:
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

    # --------------------------------------------------------------- input

    def ask(self, question: str, options: list, default: int = 0) -> int:
        """A numbered choice. Enter takes the default; anything else re-asks."""
        self.done()
        self.say()
        self.say(self.paint(question, "bold"))
        for i, option in enumerate(options, 1):
            mark = self.paint("→", "cyan") if i - 1 == default else " "
            self.say("  %s %d. %s" % (mark, i, option))
        while True:
            try:
                raw = input(self.paint("  [%d] " % (default + 1), "grey")).strip()
            except EOFError:
                return default
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1


class Spinner:
    """A status line that updates in place and never fights with real output."""

    def __init__(self, label: str):
        self.label = label
        self.extra = ""
        self.started = time.time()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._width = 0

    def start(self) -> "Spinner":
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self) -> None:
        for frame in itertools.cycle(FRAMES):
            if self._stop.is_set():
                return
            with self._lock:
                line = "%s %s %s%s" % (frame, self.label,
                                       "%.1fs" % (time.time() - self.started),
                                       ("  " + self.extra) if self.extra else "")
                self._width = len(line)
                sys.stdout.write("\r\033[2m" + line + "\033[0m\033[K")
                sys.stdout.flush()
            self._stop.wait(0.08)

    def update(self, label: str = "", extra: str = "") -> None:
        with self._lock:
            if label:
                self.label = label
            self.extra = extra

    def clear(self) -> None:
        with self._lock:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.3)
        self.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop()


class _Silent:
    def update(self, *a, **k) -> None:
        pass

    def clear(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ------------------------------------------------------------ line editing

class Line:
    """Readline with history and completion of `/commands` and `@files`."""

    def __init__(self, commands: list, files, ui: Ui):
        self.commands = commands
        self.files = files                  # callable → list of repo paths
        self.ui = ui
        self.history = os.path.join(data_dir(), "history")
        self._rl = None
        try:
            import readline
        except ImportError:
            return
        self._rl = readline
        os.makedirs(os.path.dirname(self.history), exist_ok=True)
        try:
            readline.read_history_file(self.history)
        except OSError:
            pass
        readline.set_history_length(2000)
        readline.set_completer(self._complete)
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")

    def _complete(self, text: str, state: int):
        if text.startswith("/"):
            hits = [c for c in self.commands if c.startswith(text)]
        elif text.startswith("@"):
            want = text[1:].lower()
            hits = ["@" + p for p in self.files()
                    if want in p.lower()][:60]
        else:
            return None
        return hits[state] + " " if state < len(hits) else None

    def read(self, prompt: str) -> str:
        line = input(prompt)
        if not sys.stdin.isatty():
            # Piped in: the terminal never echoed it, so the transcript would
            # show answers with no questions. Print what was read.
            print(line, flush=True)
        if self._rl:
            try:
                self._rl.write_history_file(self.history)
            except OSError:
                pass
        return line
