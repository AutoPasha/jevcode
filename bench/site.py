"""The project page, generated from the same results file as the charts.

A comparison page that is written by hand drifts from the benchmark within a
week. This one is built from `bench/results.json`, so the only way to change
what it claims is to run the benchmark again.

    python3 bench/site.py                       # writes docs/index.html
    python3 bench/site.py --results other.json

`docs/` is what GitHub Pages serves, so the page and its charts sit beside each
other and the links are plain relative paths.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compare import summarise                                    # noqa: E402

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jevcode — a coding agent driven by decisions, not prose</title>
<meta name="description" content="{tagline}">
<link rel="icon" href="assets/logo.svg">
<style>
  :root {{
    color-scheme: light dark;
    --surface: #fcfcfb; --panel: #ffffff; --text: #0b0b0b; --muted: #52514e;
    --line: #e5e4e0; --accent: #2a78d6; --accent-2: #eb6834;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --surface: #131312; --panel: #1a1a19; --text: #f6f5f2; --muted: #c3c2b7;
      --line: #2d2d2b; --accent: #3987e5; --accent-2: #d95926;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--surface); color: var(--text);
    font: 16px/1.6 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
          Helvetica, Arial, sans-serif;
  }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 48px 20px 96px; }}
  header {{ display: flex; gap: 20px; align-items: center; margin-bottom: 8px; }}
  header img {{ width: 76px; height: 76px; }}
  h1 {{ font-size: 40px; margin: 0; letter-spacing: -0.02em;
       font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  .tagline {{ color: var(--muted); font-size: 18px; margin: 4px 0 0; }}
  h2 {{ font-size: 21px; margin: 44px 0 10px; letter-spacing: -0.01em; }}
  p {{ margin: 12px 0; }}
  a {{ color: var(--accent); }}
  .row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }}
  .btn {{
    display: inline-block; padding: 9px 16px; border-radius: 9px;
    border: 1px solid var(--line); text-decoration: none; color: var(--text);
    background: var(--panel); font-size: 14.5px;
  }}
  .btn.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
  pre {{
    background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
    padding: 14px 16px; overflow-x: auto; font-size: 13.5px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14.5px; margin-top: 12px; }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 13px;
       text-transform: uppercase; letter-spacing: 0.04em; }}
  td.n {{ font-variant-numeric: tabular-nums; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 12px; margin-top: 18px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line);
           border-radius: 12px; padding: 16px; }}
  .card b {{ display: block; font-size: 27px; letter-spacing: -0.02em; }}
  .card span {{ color: var(--muted); font-size: 13.5px; }}
  figure {{ margin: 18px 0; }}
  figure img {{ width: 100%; max-width: 580px; border: 1px solid var(--line);
                border-radius: 12px; background: var(--panel); }}
  footer {{ margin-top: 56px; color: var(--muted); font-size: 13.5px;
            border-top: 1px solid var(--line); padding-top: 18px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <img src="assets/logo.svg" alt="">
  <div>
    <h1>jevcode</h1>
    <p class="tagline">{tagline}</p>
  </div>
</header>

<div class="row">
  <a class="btn primary" href="https://github.com/AutoPasha/jevcode">Source</a>
  <a class="btn" href="https://github.com/AutoPasha/jevcode#install">Install</a>
  <a class="btn" href="https://github.com/AutoPasha/jevcode/tree/main/bench">The benchmark</a>
</div>

<h2>The idea</h2>
<p>
  Jev answers typed questions — is this true, which of these, where on this
  scale — with calibrated probabilities, in about two hundred milliseconds, up
  to 256 of them in one request. It cannot write a line of code, and that is
  the point: decisions become almost free, so the agent asks about everything
  before it moves, and a small fast model does the typing under instructions it
  never chose.
</p>

<div class="cards">{cards}</div>

<h2>Measured</h2>
<p>{method}</p>
{charts}

<h2>The table</h2>
{table}

<h2>Try it</h2>
<pre>pipx install git+https://github.com/AutoPasha/jevcode
jevcode --demo        # the whole interface, no key, nothing charged
jevcode               # a session in this directory</pre>

<footer>
  Measurements from {when}. Everything on this page is generated from
  <code>bench/results.json</code> by <code>bench/site.py</code> — re-run the
  benchmark and the page changes with it. MIT licensed.
</footer>

</div>
</body>
</html>
"""

TAGLINE = "a coding agent whose decisions are made by a model that cannot write text"


def esc(text) -> str:
    return html.escape(str(text))


def build(payload: dict, out_dir: str) -> str:
    rows = payload["runs"]
    totals = summarise(rows)
    agents = list(totals)
    tasks = list(dict.fromkeys(r["task"] for r in rows))

    best = max(agents, key=lambda a: totals[a]["rate"])
    cards = "".join(
        '<div class="card"><b>%s</b><span>%s</span></div>' % (esc(value), esc(label))
        for value, label in [
            ("%d%%" % totals[best]["rate"], "of tasks solved by %s" % best),
            ("%.0fs" % totals[best]["median_seconds"], "median, start to green tests"),
            ("%d" % (totals[best]["decisions"] // max(totals[best]["runs"], 1)),
             "decisions per task"),
            ("%d" % len(tasks), "tasks, each judged by its own tests"),
        ])

    pictures = [("assets/solved.svg", "Solved, by task"),
                ("assets/speed.svg", "Seconds per task"),
                ("assets/cost.svg", "Cost per task")]
    charts = "".join(
        '<figure><img src="%s" alt="%s"></figure>' % (src, esc(alt))
        for src, alt in pictures if os.path.exists(os.path.join(out_dir, src)))

    head = ("<table><tr><th>agent</th><th>solved</th><th>median time</th>"
            "<th>cost per task</th></tr>")
    body = ""
    for agent in sorted(agents, key=lambda a: -totals[a]["rate"]):
        entry = totals[agent]
        money = ("%.2f %s" % (entry["cost"] / max(entry["runs"], 1), entry["currency"])
                 if entry["cost"] else "—")
        body += ("<tr><td>%s</td><td class=\"n\">%d/%d (%d%%)</td>"
                 "<td class=\"n\">%.1fs</td><td class=\"n\">%s</td></tr>"
                 % (esc(agent), entry["passed"], entry["runs"], entry["rate"],
                    entry["median_seconds"], esc(money)))
    table = head + body + "</table>"

    method = payload.get("note") or (
        "Every agent gets an untouched copy of the same directory, the same "
        "sentence and the same time limit, and the project's own tests decide. "
        "No partial credit, no prompt tuned per agent.")

    page = PAGE.format(tagline=esc(TAGLINE), cards=cards, charts=charts,
                       table=table, method=esc(method),
                       when=esc(payload.get("when", "an unrecorded run")))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(here, "results.json"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(here), "docs"))
    args = ap.parse_args()
    if not os.path.exists(args.results):
        print("no results yet: run bench/compare.py first", file=sys.stderr)
        return 2
    with open(args.results, encoding="utf-8") as fh:
        payload = json.load(fh)
    print("wrote %s" % build(payload, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
