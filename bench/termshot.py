"""A terminal screenshot as SVG, replayed from a recording of a real session.

The README needs a picture of the thing running, and a PNG of a terminal is
blurry on every display it was not taken on. So the session is recorded with
asciinema, replayed here through a small screen buffer that understands the
escape sequences this agent actually emits, and written out as text in an SVG.
Crisp at any zoom, a few kilobytes, selectable, and diffable in review.

    asciinema rec demo.cast --cols 96 --rows 30 -c "jevcode --demo"
    python3 bench/termshot.py demo.cast --out docs/assets/session.svg

It is a screenshot, not an emulator: enough of the escape codes to be faithful
to this program's output, and nothing more.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys

# Dracula-ish, chosen to stay legible on both a light and a dark page.
BACKGROUND = "#232733"
FOREGROUND = "#e6e6e6"
ANSI = {
    30: "#4d5566", 31: "#ff6b6b", 32: "#5ddd9b", 33: "#ffd479", 34: "#6aa9ff",
    35: "#e59bd6", 36: "#69d9e0", 37: "#e6e6e6",
    90: "#8b93a6", 91: "#ff8a8a", 92: "#87e8b4", 93: "#ffe0a3", 94: "#93c1ff",
    95: "#efb6e4", 96: "#9ae7ec", 97: "#ffffff",
}
CELL_W, CELL_H, PAD = 8.4, 19.0, 18.0
SGR = re.compile(r"\x1b\[([0-9;]*)m")
CSI = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")


class Screen:
    """Rows of (char, colour, bold, dim). Only what this program produces."""

    def __init__(self, cols: int, rows: int):
        self.cols, self.rows = cols, rows
        self.grid = [self._blank() for _ in range(rows)]
        self.x = self.y = 0
        self.colour, self.bold, self.dim = FOREGROUND, False, False

    def _blank(self) -> list:
        return [(" ", FOREGROUND, False, False) for _ in range(self.cols)]

    def feed(self, text: str) -> None:
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                match = CSI.match(text, i)
                if match:
                    self._escape(match.group(1), match.group(2))
                    i = match.end()
                    continue
                i += 1
                continue
            if ch == "\n":
                self._newline()
            elif ch == "\r":
                self.x = 0
            elif ch == "\b":
                self.x = max(0, self.x - 1)
            elif ch == "\t":
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif ch >= " ":
                self._put(ch)
            i += 1

    def _escape(self, params: str, kind: str) -> None:
        numbers = [int(p) for p in params.split(";") if p.isdigit()]
        if kind == "m":
            self._sgr(numbers or [0])
        elif kind == "K":                       # erase to end of line
            for x in range(self.x, self.cols):
                self.grid[self.y][x] = (" ", FOREGROUND, False, False)
        elif kind == "J":                       # erase screen
            self.grid = [self._blank() for _ in range(self.rows)]
            self.x = self.y = 0
        elif kind == "H":
            self.y = min(self.rows - 1, max(0, (numbers[0] if numbers else 1) - 1))
            self.x = min(self.cols - 1, max(0, (numbers[1] if len(numbers) > 1 else 1) - 1))
        elif kind == "A":
            self.y = max(0, self.y - (numbers[0] if numbers else 1))
        elif kind == "B":
            self.y = min(self.rows - 1, self.y + (numbers[0] if numbers else 1))
        elif kind in "CD":
            step = (numbers[0] if numbers else 1) * (1 if kind == "C" else -1)
            self.x = min(self.cols - 1, max(0, self.x + step))

    def _sgr(self, numbers: list) -> None:
        for n in numbers:
            if n == 0:
                self.colour, self.bold, self.dim = FOREGROUND, False, False
            elif n == 1:
                self.bold = True
            elif n == 2:
                self.dim = True
            elif n in (22, 39):
                self.bold = self.dim = False if n == 22 else self.dim
                if n == 39:
                    self.colour = FOREGROUND
            elif n in ANSI:
                self.colour = ANSI[n]

    def _put(self, ch: str) -> None:
        if self.x >= self.cols:
            self._newline()
        self.grid[self.y][self.x] = (ch, self.colour, self.bold, self.dim)
        self.x += 1

    def _newline(self) -> None:
        self.x = 0
        if self.y + 1 < self.rows:
            self.y += 1
        else:
            self.grid.pop(0)
            self.grid.append(self._blank())

    # ------------------------------------------------------------- output

    def trimmed(self) -> list:
        rows = [row for row in self.grid]
        while rows and all(cell[0] == " " for cell in rows[-1]):
            rows.pop()
        return rows

    def to_svg(self, title: str = "") -> str:
        rows = self.trimmed()
        width = PAD * 2 + self.cols * CELL_W
        height = PAD * 2 + len(rows) * CELL_H + 26
        out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
               'viewBox="0 0 %.0f %.0f" role="img" aria-label="%s">'
               % (width, height, width, height, html.escape(title or "a jevcode session")),
               '<style>text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'
               '"DejaVu Sans Mono",monospace;font-size:13px;white-space:pre}'
               '.b{font-weight:600}</style>',
               '<rect width="%.0f" height="%.0f" rx="10" fill="%s"/>'
               % (width, height, BACKGROUND)]
        for i, colour in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            out.append('<circle cx="%.0f" cy="17" r="5.5" fill="%s"/>'
                       % (20 + i * 18, colour))
        if title:
            out.append('<text x="%.0f" y="21" fill="#8b93a6" text-anchor="middle" '
                       'font-size="11.5">%s</text>' % (width / 2, html.escape(title)))

        top = PAD + 26
        for y, row in enumerate(rows):
            baseline = top + (y + 1) * CELL_H - 5
            for run in _runs(row):
                text, x, colour, bold, dim = run
                out.append('<text x="%.1f" y="%.1f" fill="%s"%s%s>%s</text>'
                           % (PAD + x * CELL_W, baseline, colour,
                              ' class="b"' if bold else "",
                              ' opacity="0.62"' if dim else "",
                              html.escape(text)))
        out.append("</svg>")
        return "\n".join(out)


def _runs(row: list):
    """Consecutive cells sharing a style become one <text> — far fewer nodes."""
    start, current = 0, None
    for x, cell in enumerate(row + [None]):
        style = None if cell is None else cell[1:]
        if style != current:
            if current is not None:
                text = "".join(c[0] for c in row[start:x]).rstrip()
                if text.strip():
                    yield (text, start) + current
            start, current = x, style
    return


def from_cast(path: str) -> tuple:
    with open(path, encoding="utf-8") as fh:
        lines = [line for line in fh.read().splitlines() if line.strip()]
    header = json.loads(lines[0])
    body = "".join(json.loads(line)[2] for line in lines[1:]
                   if json.loads(line)[1] == "o")
    return header.get("width", 96), header.get("height", 30), body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cast", help="an asciinema v2 recording, or - for raw ANSI on stdin")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="jevcode --demo")
    ap.add_argument("--cols", type=int, default=0)
    ap.add_argument("--rows", type=int, default=0)
    args = ap.parse_args()

    if args.cast == "-":
        cols, rows, body = args.cols or 96, args.rows or 40, sys.stdin.read()
    else:
        cols, rows, body = from_cast(args.cast)
        cols, rows = args.cols or cols, args.rows or rows

    screen = Screen(cols, rows)
    screen.feed(body)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(screen.to_svg(args.title) + "\n")
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
