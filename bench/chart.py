"""Charts for the README, drawn from `bench/results.json` and nothing else.

SVG by hand rather than a plotting library, for three reasons: the package has
no dependencies and the benchmark should not add one, GitHub renders SVG
crisply at any zoom, and a chart generated from the results file cannot drift
away from the numbers it claims to show. Re-run this after every benchmark and
the pictures are the measurements.

    python3 bench/chart.py                      # reads bench/results.json
    python3 bench/chart.py --results other.json --out docs/assets

One measure per chart — solved, seconds, money never share a scale. Colours
are the two leading slots of a palette checked for colour-blind separation in
both light and dark mode, and every bar carries its number, so nothing depends
on telling two hues apart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import summarise                                    # noqa: E402

LIGHT = {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e",
         "series": ["#2a78d6", "#eb6834", "#1baf7a"]}
DARK = {"surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7",
        "series": ["#3987e5", "#d95926", "#199e70"]}

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
            "text{fill:%s}" % LIGHT["text"], ".m{fill:%s}" % LIGHT["muted"],
            "rect.bg{fill:%s}" % LIGHT["surface"]]
    dark = ["text{fill:%s}" % DARK["text"], ".m{fill:%s}" % DARK["muted"],
            "rect.bg{fill:%s}" % DARK["surface"]]
    for i in range(series_count):
        rows.append(".s%d{fill:%s}" % (i + 1, LIGHT["series"][i % 3]))
        dark.append(".s%d{fill:%s}" % (i + 1, DARK["series"][i % 3]))
    rows.append("@media (prefers-color-scheme: dark){%s}" % "".join(dark))
    return "<style>%s</style>" % "".join(rows)


def esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _text(cls: str, x, y, body: str, colour: str, anchor: str = "") -> str:
    end = ' text-anchor="%s"' % anchor if anchor else ""
    return '<text class="%s" x="%.1f" y="%.1f"%s fill="%s">%s</text>' % (
        cls, x, y, end, colour, esc(body))


def grouped_bars(title: str, note: str, categories: list, series: list,
                 fmt=lambda v: "%g" % v, width: int = 580) -> str:
    """Horizontal grouped bars: a category per row, one bar per agent.

    Horizontal because the row labels are words, and words read badly rotated.
    """
    pad_left, pad_right = 118, 64
    bar, gap, group_gap = 18, 4, 16
    # A single series needs no legend: the title already names it.
    legend = len(series) > 1
    top = 46 + (16 if note else 0) + (26 if legend else 6)
    inner = max(len(series), 1)
    height = top + len(categories) * (inner * bar + (inner - 1) * gap + group_gap) + 18
    span = width - pad_left - pad_right
    values = [v for s in series for v in s["values"] if v is not None]
    biggest = max(values) if values and max(values) > 0 else 1

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d" role="img" class="s" aria-label="%s">'
           % (width, height, width, height, esc(title)),
           style(inner),
           '<rect class="bg" x="0" y="0" width="%d" height="%d" fill="%s"/>'
           % (width, height, LIGHT["surface"]),
           _text("t", 16, 26, title, LIGHT["text"])]
    if note:
        out.append(_text("c m", 16, 44, note, LIGHT["muted"]))

    if legend:
        legend_y = top - 18
        x = 16
        for i, s in enumerate(series):
            out.append('<rect class="s%d" x="%.1f" y="%.1f" width="10" height="10" '
                       'rx="2" fill="%s"/>'
                       % (i + 1, x, legend_y - 9, LIGHT["series"][i % 3]))
            out.append(_text("c m", x + 15, legend_y, s["name"], LIGHT["muted"]))
            x += 15 + 7.2 * len(s["name"]) + 18

    y = top
    for row, category in enumerate(categories):
        middle = y + (inner * bar + (inner - 1) * gap) / 2 + 4
        out.append(_text("l m", pad_left - 12, middle, category, LIGHT["muted"], "end"))
        for i, s in enumerate(series):
            value = s["values"][row]
            if value is None:
                out.append(_text("c m", pad_left + 2, y + bar - 4, "no run",
                                 LIGHT["muted"]))
                y += bar + gap
                continue
            length = max(2.0, span * (value / biggest))
            out.append('<rect class="s%d" x="%d" y="%.1f" width="%.1f" height="%d" '
                       'rx="4" fill="%s"/>'
                       % (i + 1, pad_left, y, length, bar, LIGHT["series"][i % 3]))
            out.append(_text("v", pad_left + length + 8, y + bar - 4, fmt(value),
                             LIGHT["text"]))
            y += bar + gap
        y += group_gap - gap
    out.append("</svg>")
    return "\n".join(out)


def charts(payload: dict, out_dir: str) -> list:
    rows = payload["runs"]
    agents = list(dict.fromkeys(r["agent"] for r in rows))
    tasks = list(dict.fromkeys(r["task"] for r in rows))
    totals = summarise(rows)
    os.makedirs(out_dir, exist_ok=True)

    def runs_of(agent, task):
        return [r for r in rows if r["agent"] == agent and r["task"] == task]

    def per_task(pick):
        return [{"name": a, "values": [pick(a, t) for t in tasks]} for a in agents]

    def solved(agent, task):
        got = runs_of(agent, task)
        return round(100.0 * sum(1 for r in got if r["passed"]) / len(got)) if got else None

    def seconds(agent, task):
        got = runs_of(agent, task)
        return round(sum(r["seconds"] for r in got) / len(got), 1) if got else None

    def cost(agent, task):
        got = runs_of(agent, task)
        return round(sum(r.get("cost") or 0 for r in got) / len(got), 3) if got else None

    files = [
        ("solved.svg", grouped_bars(
            "Solved, by task", "the project's own tests decide; no partial credit",
            tasks, per_task(solved), fmt=lambda v: "%d%%" % v)),
        ("speed.svg", grouped_bars(
            "Seconds per task", "wall clock, averaged over the runs; lower is better",
            tasks, per_task(seconds), fmt=lambda v: "%.0fs" % v)),
        ("rate.svg", grouped_bars(
            "Solved overall", "every task, every run",
            ["all tasks"],
            [{"name": a, "values": [totals[a]["rate"]]} for a in agents],
            fmt=lambda v: "%d%%" % v)),
    ]
    money = [v for a in agents for v in (cost(a, t) for t in tasks) if v]
    if money:
        currency = next((r.get("currency") for r in rows if r.get("currency")), "")
        files.append(("cost.svg", grouped_bars(
            "Cost per task, %s" % (currency or "in the account's currency"),
            "what the models charged for the run", tasks, per_task(cost),
            fmt=lambda v: "%.2f" % v)))

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
