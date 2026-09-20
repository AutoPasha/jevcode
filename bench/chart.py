"""Charts for the README, drawn from `bench/results.json` and nothing else.

SVG by hand rather than a plotting library, for three reasons: the package has
no dependencies and the benchmark should not add one, GitHub renders SVG
crisply at any zoom, and a chart generated from the results file cannot drift
away from the numbers it claims to show. Re-run this after every benchmark and
the pictures are the measurements.

    python3 bench/chart.py                      # reads bench/results.json
    python3 bench/chart.py --results other.json --out docs/assets

Four pictures, each answering one question a reader actually asks:

    scoreboard.svg   who wins, on every measure at once
    leaderboard.svg  the running order, best first
    speed.svg        where the time goes, task by task, and who took each one
    cost.svg         what the run charged

One measure per chart — solved, seconds, money never share a scale. Colours
are the two leading slots of a palette checked for colour-blind separation in
both light and dark mode, and every bar carries its number, so nothing depends
on telling two hues apart.

A chart where every bar is the same length tells a reader nothing, and nine
rows of them tell them nothing nine times. `useful()` throws such a chart away;
the fact it was hiding goes into the scoreboard as a line of text instead.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import summarise                                    # noqa: E402

LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e",
         "line": "#dcdbd6", "win": "#e8f3ec",
         "series": ["#2a78d6", "#eb6834", "#1baf7a", "#8b5cf6"]}
DARK = {"surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7",
        "line": "#3a3a37", "win": "#1c2f25",
        "series": ["#3987e5", "#d95926", "#199e70", "#9d7bf0"]}

FONT = ("ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', "
        "Helvetica, Arial, sans-serif")


def style(series_count: int) -> str:
    """Both modes declared in CSS, with literal light colours underneath.

    Every coloured thing carries a plain `fill=` as well as a class. A browser
    applies the stylesheet and gets dark mode; a renderer that ignores CSS —
    some markdown viewers, PDF exporters, thumbnailers — still gets the light
    palette rather than a black rectangle, which is what CSS custom properties
    on their own would have produced.
    """
    rows = [".s{font-family:%s}" % FONT,
            ".t{font-weight:600;font-size:15px}",
            ".l{font-size:12.5px}", ".v{font-size:12.5px;font-weight:600}",
            ".c{font-size:11.5px}",
            ".h{font-size:12.5px;font-weight:600}",
            ".b{font-size:14px;font-weight:600}",
            "text{fill:%s}" % LIGHT["text"], ".m{fill:%s}" % LIGHT["muted"],
            "rect.bg{fill:%s}" % LIGHT["surface"],
            "rect.win{fill:%s}" % LIGHT["win"],
            "line.g,line.ax{stroke:%s}" % LIGHT["line"]]
    dark = ["text{fill:%s}" % DARK["text"], ".m{fill:%s}" % DARK["muted"],
            "rect.bg{fill:%s}" % DARK["surface"],
            "rect.win{fill:%s}" % DARK["win"],
            "line.g,line.ax{stroke:%s}" % DARK["line"]]
    for i in range(max(series_count, 1)):
        rows.append(".s%d{fill:%s}" % (i + 1, LIGHT["series"][i % 4]))
        rows.append(".f%d{fill:%s}" % (i + 1, LIGHT["series"][i % 4]))
        dark.append(".s%d{fill:%s}" % (i + 1, DARK["series"][i % 4]))
        dark.append(".f%d{fill:%s}" % (i + 1, DARK["series"][i % 4]))
    rows.append("@media (prefers-color-scheme: dark){%s}" % "".join(dark))
    return "<style>%s</style>" % "".join(rows)


def esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(cls: str, x, y, body: str, colour: str, anchor: str = "") -> str:
    end = ' text-anchor="%s"' % anchor if anchor else ""
    return '<text class="%s" x="%.1f" y="%.1f"%s fill="%s">%s</text>' % (
        cls, x, y, end, colour, esc(body))


def _open(title: str, width: int, height: int, series: int) -> list:
    return ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" role="img" class="s" aria-label="%s">'
            % (width, height, width, height, esc(title)),
            style(series),
            '<rect class="bg" x="0" y="0" width="%d" height="%d" fill="%s"/>'
            % (width, height, LIGHT["surface"])]


def _legend(out: list, series: list, x: float, y: float) -> None:
    for i, s in enumerate(series):
        out.append('<rect class="s%d" x="%.1f" y="%.1f" width="10" height="10" '
                   'rx="2" fill="%s"/>' % (i + 1, x, y - 9, LIGHT["series"][i % 4]))
        out.append(_text("c m", x + 15, y, s["name"], LIGHT["muted"]))
        x += 15 + 7.2 * len(s["name"]) + 18


def useful(series: list) -> bool:
    """Is there anything to see, or is every bar the same length?

    Two agents that both pass every task make eighteen identical bars. That is
    not a comparison, it is wallpaper. The caller drops the chart and states
    the fact in words instead.
    """
    if len(series) < 2:
        return any(v is not None for s in series for v in s["values"])
    for row in range(len(series[0]["values"])):
        column = [s["values"][row] for s in series]
        if len(set(column)) > 1:
            return True
    return False


def nice_top(biggest: float) -> float:
    """A round number at or above the tallest bar, for a readable axis."""
    if biggest <= 0:
        return 1.0
    step = 10 ** (len(str(int(biggest))) - 1)
    for factor in (1, 2, 2.5, 5, 10):
        top = step * factor
        if top >= biggest:
            return float(top)
    return float(biggest)


def scoreboard(title: str, note: str, agents: list, rows: list,
               width: int = 720) -> str:
    """The table a reader looks at first: every measure, every agent, one grid.

    Rows are measures, columns are agents, and the cell that wins its row is
    filled and set bold. A row where one side has no number is shown but never
    highlighted — an agent does not win a measure by not reporting it.
    """
    pad = 16
    label_w = 230
    head_h = 46
    row_h = 44
    top = 34 + (18 if note else 0) + 12
    height = top + head_h + len(rows) * row_h + 14
    col_w = (width - pad * 2 - label_w) / max(len(agents), 1)

    out = _open(title, width, height, len(agents))
    out.append(_text("t", pad, 26, title, LIGHT["text"]))
    if note:
        out.append(_text("c m", pad, 44, note, LIGHT["muted"]))

    head_y = top + 16
    for i, agent in enumerate(agents):
        x = pad + label_w + col_w * i
        out.append('<rect class="s%d" x="%.1f" y="%.1f" width="10" height="10" '
                   'rx="2" fill="%s"/>'
                   % (i + 1, x, head_y - 9, LIGHT["series"][i % 4]))
        # "jevcode · Jev + MiniMax-M2.7" is wider than any column worth having,
        # so the name goes on one line and what it writes with on the next.
        name, _, writer = agent.partition(" · ")
        out.append(_text("h", x + 16, head_y, name, LIGHT["text"]))
        if writer:
            out.append(_text("c m", x + 16, head_y + 15, writer, LIGHT["muted"]))
    out.append('<line class="ax" x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
               % (pad, top + head_h - 6, width - pad, top + head_h - 6, LIGHT["line"]))

    y = top + head_h
    for row in rows:
        best = row.get("best")
        for i, agent in enumerate(agents):
            x = pad + label_w + col_w * i
            value, under = row["cells"][i]
            if best == i:
                out.append('<rect class="win" x="%.1f" y="%.1f" width="%.1f" '
                           'height="%.1f" rx="6" fill="%s"/>'
                           % (x - 8, y + 2, col_w - 6, row_h - 8, LIGHT["win"]))
            out.append(_text("b" if best == i else "l", x, y + 22, value,
                             LIGHT["text"]))
            if under:
                out.append(_text("c m", x, y + 37, under, LIGHT["muted"]))
        out.append(_text("l", pad, y + 22, row["name"], LIGHT["text"]))
        if row.get("note"):
            out.append(_text("c m", pad, y + 37, row["note"], LIGHT["muted"]))
        y += row_h
        if row is not rows[-1]:
            out.append('<line class="g" x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                       'stroke="%s"/>' % (pad, y - 4, width - pad, y - 4, LIGHT["line"]))
    out.append("</svg>")
    return "\n".join(out)


def leaderboard(title: str, note: str, entries: list, width: int = 720) -> str:
    """Running order, best first: one bar per contestant, longest on top.

    Entries are `(name, value, caption, series_index)`. The caption rides at
    the end of the bar — "(8 solved)" — because the reader wants the count and
    the rate in the same glance.
    """
    pad = 16
    label_w = 210
    bar, gap = 26, 14
    top = 34 + (18 if note else 0) + 14
    height = top + len(entries) * (bar + gap) + 34
    span = width - pad - label_w - 96
    biggest = nice_top(max([e[1] for e in entries] or [1]))

    out = _open(title, width, height, 3)
    out.append(_text("t", pad, 26, title, LIGHT["text"]))
    if note:
        out.append(_text("c m", pad, 44, note, LIGHT["muted"]))

    floor = top + len(entries) * (bar + gap) - gap + 6
    for part in (0, 0.5, 1):
        x = label_w + span * part
        out.append('<line class="g" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                   'stroke="%s"/>' % (x, top - 6, x, floor, LIGHT["line"]))
        out.append(_text("c m", x, floor + 18, "%g" % (biggest * part),
                         LIGHT["muted"], "middle"))

    y = top
    for name, value, caption, slot in entries:
        length = max(2.0, span * (value / biggest))
        out.append(_text("l m", label_w - 12, y + bar - 8, name, LIGHT["muted"], "end"))
        out.append('<rect class="s%d" x="%.1f" y="%.1f" width="%.1f" height="%d" '
                   'rx="4" fill="%s"/>'
                   % (slot + 1, label_w, y, length, bar, LIGHT["series"][slot % 4]))
        out.append(_text("v", label_w + length + 10, y + bar - 8, caption,
                         LIGHT["text"]))
        y += bar + gap
    out.append("</svg>")
    return "\n".join(out)


def columns(title: str, note: str, categories: list, series: list, fmt,
            verdicts: list, width: int = 720) -> str:
    """Vertical grouped columns, with the winner of each category named below.

    Vertical because the eye compares heights side by side better than bar
    lengths stacked down a page, and because the row of verdicts underneath —
    "jevcode wins", "dead heat" — is the sentence the chart is really making.
    """
    pad = 16
    top = 34 + (18 if note else 0) + 26
    plot_h = 150
    foot = 52
    height = top + plot_h + foot
    left = 38
    span = width - pad - left - 10
    slot = span / max(len(categories), 1)
    col = min(26.0, (slot - 14) / max(len(series), 1))
    biggest = nice_top(max([v for s in series for v in s["values"] if v is not None]
                           or [1]))

    out = _open(title, width, height, len(series))
    out.append(_text("t", pad, 26, title, LIGHT["text"]))
    if note:
        out.append(_text("c m", pad, 44, note, LIGHT["muted"]))
    _legend(out, series, pad, top - 16)

    floor = top + plot_h
    for part in (0, 0.5, 1):
        y = floor - plot_h * part
        out.append('<line class="g" x1="%.1f" y1="%.1f" x2="%d" y2="%.1f" '
                   'stroke="%s"/>' % (left, y, width - pad, y, LIGHT["line"]))
        out.append(_text("c m", left - 8, y + 4, "%g" % (biggest * part),
                         LIGHT["muted"], "end"))

    for row, category in enumerate(categories):
        middle = left + slot * row + slot / 2
        start = middle - (col * len(series) + 2 * (len(series) - 1)) / 2
        for i, s in enumerate(series):
            value = s["values"][row]
            x = start + i * (col + 2)
            if value is None:
                out.append(_text("c m", x + col / 2, floor - 6, "—",
                                 LIGHT["muted"], "middle"))
                continue
            tall = max(2.0, plot_h * (value / biggest))
            out.append('<rect class="s%d" x="%.1f" y="%.1f" width="%.1f" '
                       'height="%.1f" rx="3" fill="%s"/>'
                       % (i + 1, x, floor - tall, col, tall,
                          LIGHT["series"][i % 4]))
            out.append(_text("c", x + col / 2, floor - tall - 5, fmt(value),
                             LIGHT["text"], "middle"))
        out.append(_text("l", middle, floor + 18, category, LIGHT["text"], "middle"))
        verdict, slot_i = verdicts[row]
        cls = "c m" if slot_i is None else "c f%d" % (slot_i + 1)
        colour = LIGHT["muted"] if slot_i is None else LIGHT["series"][slot_i % 4]
        out.append(_text(cls, middle, floor + 34, verdict, colour, "middle"))
    out.append("</svg>")
    return "\n".join(out)


def _stats(rows: list, agents: list, tasks: list):
    def runs_of(agent, task=None):
        return [r for r in rows if r["agent"] == agent
                and (task is None or r["task"] == task)]
    return runs_of


def charts(payload: dict, out_dir: str) -> list:
    rows = payload["runs"]
    agents = list(dict.fromkeys(r["agent"] for r in rows))
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    totals = summarise(rows)
    runs_of = _stats(rows, agents, tasks)
    os.makedirs(out_dir, exist_ok=True)
    # Four contestants make a readable leaderboard and an unreadable pair of
    # columns, so the head-to-head pictures take the two the run is actually
    # about: same writer model, different harness.
    headline = [a for a in (payload.get("headline") or agents[:2]) if a in agents]
    if len(headline) < 2:
        headline = agents[:2]

    def seconds(agent, task):
        got = runs_of(agent, task)
        return round(sum(r["seconds"] for r in got) / len(got), 1) if got else None

    def solved(agent, task):
        got = runs_of(agent, task)
        return round(100.0 * sum(1 for r in got if r["passed"]) / len(got)) if got else None

    def cost(agent, task):
        got = runs_of(agent, task)
        return round(sum(r.get("cost") or 0 for r in got) / len(got), 3) if got else None

    def per_task(pick):
        return [{"name": a, "values": [pick(a, t) for t in tasks]} for a in agents]

    def median(agent):
        got = sorted(r["seconds"] for r in runs_of(agent))
        return round(got[len(got) // 2], 1) if got else None

    def total(agent):
        return round(sum(r["seconds"] for r in runs_of(agent)), 0)

    def slowest(agent):
        got = [r["seconds"] for r in runs_of(agent)]
        return round(max(got), 1) if got else None

    def calls(agent):
        got = runs_of(agent)
        asked = sum(r.get("requests") or 0 for r in got)
        drafts = sum(r.get("drafts") or 0 for r in got)
        if not asked and not drafts:
            return None
        return round((asked + drafts) / len(got), 1)

    files = []

    # --- the scoreboard --------------------------------------------------
    def row_of(name, note, values, direction, fmt, under=lambda a: ""):
        cells = [(fmt(values[i]) if values[i] is not None else "not reported",
                  under(agents[i])) for i in range(len(agents))]
        numbers = [(i, v) for i, v in enumerate(values) if v is not None]
        best = None
        if len(numbers) > 1 and len({v for _, v in numbers}) > 1:
            pick = max if direction == "high" else min
            best = pick(numbers, key=lambda pair: pair[1])[0]
        return {"name": name, "note": note, "cells": cells, "best": best}

    everyone = agents
    rates = [totals[a]["rate"] for a in agents]
    board = [
        row_of("Solved", "the project's own tests decide",
               rates, "high", lambda v: "%d%%" % v,
               under=lambda a: "%d of %d tasks" % (totals[a]["passed"],
                                                   totals[a]["runs"])),
        row_of("Median task", "half the tasks are quicker than this",
               [median(a) for a in agents], "low", lambda v: "%.1fs" % v),
        row_of("Slowest task", "the tail is what a person waits through",
               [slowest(a) for a in agents], "low", lambda v: "%.0fs" % v),
        row_of("Whole benchmark", "every task, end to end",
               [total(a) for a in agents], "low", lambda v: "%.0fs" % v),
        row_of("Model calls per task", "requests to a model, drafts included",
               [calls(a) for a in agents], "low", lambda v: "%.1f" % v),
    ]
    files.append(("scoreboard.svg", scoreboard(
        payload.get("title") or "Every contestant, measure by measure",
        payload.get("subtitle")
        or "same nine tasks, one attempt each; the best cell in a row is filled",
        agents, board, width=880)))

    # Everything past here compares two agents that share a writer, so what is
    # left between them is the harness.
    agents = headline

    # --- the leaderboard, everybody who ran ------------------------------
    order = sorted(everyone, key=lambda a: (-totals[a]["rate"], median(a) or 0))
    entries = [(a, totals[a]["rate"],
                "%d%%  (%d solved)" % (totals[a]["rate"], totals[a]["passed"]),
                everyone.index(a)) for a in order]
    files.append(("leaderboard.svg", leaderboard(
        "Leaderboard — solved out of %d tasks" % len(tasks),
        "one attempt per task; a suite made green by rewriting its own tests does not count",
        entries)))

    # --- seconds per task, with the winner of each named ------------------
    speed = per_task(seconds)
    verdicts = []
    for row in range(len(tasks)):
        column = [(i, s["values"][row]) for i, s in enumerate(speed)
                  if s["values"][row] is not None]
        if len(column) < 2:
            verdicts.append(("one runner", None))
            continue
        column.sort(key=lambda pair: pair[1])
        quick, second = column[0], column[1]
        gap = (second[1] - quick[1]) / max(second[1], 0.001)
        if gap < 0.1:
            verdicts.append(("dead heat", None))
        else:
            verdicts.append(("×%.1f" % (second[1] / max(quick[1], 0.001)),
                             quick[0]))
    files.append(("speed.svg", columns(
        "Seconds per task", "wall clock, one attempt each; shorter is better",
        tasks, speed, lambda v: "%.0f" % v, verdicts)))

    # --- solved per task, only when it separates anybody ------------------
    per_task_solved = per_task(solved)
    if useful(per_task_solved):
        split = []
        for row in range(len(tasks)):
            column = [(i, s["values"][row]) for i, s in enumerate(per_task_solved)
                      if s["values"][row] is not None]
            top = max((v for _, v in column), default=0)
            winners = [i for i, v in column if v == top]
            split.append(("dead heat", None) if len(winners) != 1
                         else ("only one", winners[0]))
        files.append(("solved.svg", columns(
            "Solved, by task", "the project's own tests decide; no partial credit",
            tasks, per_task_solved, lambda v: "%d%%" % v, split)))

    # --- money, when anybody reported any --------------------------------
    money = per_task(cost)
    if any(v for s in money for v in s["values"]):
        currency = next((r.get("currency") for r in rows if r.get("currency")), "")
        billed = [{"name": s["name"],
                   "values": [v if v else None for v in s["values"]]} for s in money]
        verdict = [("", None)] * len(tasks)
        files.append(("cost.svg", columns(
            "Cost per task, %s" % (currency or "in the account's currency"),
            "what the models charged; a dash means the runner does not report it",
            tasks, billed, lambda v: "%.2f" % v, verdict)))

    written = []
    for name, body in files:
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        written.append(path)
    return written


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(here, "results.json"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(here), "docs", "assets"))
    args = ap.parse_args()
    if not os.path.exists(args.results):
        print("no results yet: run bench/compare.py first", file=sys.stderr)
        return 2
    with open(args.results, encoding="utf-8") as fh:
        payload = json.load(fh)
    for path in charts(payload, args.out):
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
