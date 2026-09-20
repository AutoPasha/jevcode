"""The command line.

`jevcode` on its own opens a conversation in the current directory; everything
else is a subcommand, and the subcommands are the ones people already know from
other terminal agents — run, auth, models, session, stats, init. Nothing here
is novel on purpose: an agent that invents its own vocabulary makes you learn
it before you can find out whether it is any good.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import locate, session as sessions
from .config import (Config, auth_file, credentials, key_for, save_credential,
                     user_file, write_instructions)
from .engine import Agent
from .permission import Permission
from .repo import Repo
from .systemone import SystemOne, Usage
from .trace import Trace
from .ui import Ui
from .writer import Writer, WriterUsage

EPILOG = """examples:
  jevcode                                open a conversation here
  jevcode ../other-project               ...or somewhere else
  jevcode -c                             carry on where you left off
  jevcode run "make the parser accept a trailing comma"
  jevcode run -f src/parse.py "handle the empty case"
  jevcode where "the retry backoff"      find it without changing anything
  jevcode plan "add a --json flag"       see the moves it would make
  jevcode auth login                     save the two keys it needs
  jevcode stats                          what it has cost you

keys (the environment wins over `auth login`):
  JEVCODE_SYSTEMONE_KEY / TYPESAFE_API_KEY   the model that decides
  JEVCODE_WRITER_KEY / OPENAI_API_KEY        the model that writes
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jevcode", epilog=EPILOG,
        description="A coding agent whose decisions are made by a model that cannot write text.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    _common(p)
    p.add_argument("-c", "--continue", dest="resume_last", action="store_true",
                   help="resume the most recent session")
    p.add_argument("-s", "--session", default="", help="resume a session by id")
    p.add_argument("--demo", action="store_true",
                   help="tour the interface with canned answers, no key and no network")
    p.add_argument("--version", action="store_true")

    subs = p.add_subparsers(dest="command")

    run = subs.add_parser("run", help="run one task and exit")
    run.add_argument("message", nargs="*")
    run.add_argument("-f", "--file", action="append", default=[],
                     help="attach a file to the message")
    run.add_argument("--format", choices=("text", "json"), default="text")
    run.add_argument("--dry-run", action="store_true",
                     help="show the change it would make, change nothing")
    run.add_argument("--no-commands", action="store_true",
                     help="never run anything, not even the tests")
    run.add_argument("--trace", metavar="FILE", help="write every decision to a jsonl file")
    _common(run, later=True)

    where = subs.add_parser("where", help="find where something lives")
    where.add_argument("question", nargs="*")
    _common(where, later=True)

    plan = subs.add_parser("plan", help="show the moves it would make")
    plan.add_argument("question", nargs="*")
    _common(plan, later=True)

    auth = subs.add_parser("auth", help="save or show the keys")
    auth.add_argument("action", nargs="?", default="list",
                      choices=("login", "list", "ls", "logout"))
    auth.add_argument("which", nargs="?", default="",
                      choices=("", "systemone", "writer"))

    models = subs.add_parser("models", help="which models are configured")
    _common(models, later=True)

    ses = subs.add_parser("session", help="list, show or export sessions")
    ses.add_argument("action", nargs="?", default="list",
                     choices=("list", "ls", "show", "export", "delete"))
    ses.add_argument("id", nargs="?", default="")
    ses.add_argument("--format", choices=("table", "json"), default="table")
    _common(ses, later=True)

    stats = subs.add_parser("stats", help="what it has cost you")
    stats.add_argument("--days", type=int, default=0)
    _common(stats, later=True)

    init = subs.add_parser("init", help="write an AGENTS.md for this project")
    _common(init, later=True)

    cfg = subs.add_parser("config", help="show the settings and where they came from")
    _common(cfg, later=True)
    return p


def _common(p: argparse.ArgumentParser, later: bool = False) -> None:
    """Flags that mean the same thing everywhere.

    A subcommand repeats them so they can be typed after it, which is where the
    hand expects them. `SUPPRESS` is what keeps that from undoing a flag typed
    *before* the subcommand: an option nobody passed leaves no attribute
    behind, so the value parsed by the top level survives.
    """
    default = argparse.SUPPRESS if later else None
    flag = argparse.SUPPRESS if later else False
    p.add_argument("-C", "--directory", dest="directory", default=default,
                   help="repository to work in")
    p.add_argument("-m", "--model", dest="writer_model", default=default,
                   help="the model that writes the code")
    p.add_argument("--jev-model", dest="jev_model", default=default,
                   help="the model that makes the decisions")
    p.add_argument("-n", "--candidates", type=int, default=default,
                   help="drafts per edit (default 6)")
    p.add_argument("--max-steps", type=int, default=default)
    p.add_argument("--permission", choices=("ask", "allow", "deny"), default=default,
                   help="before edits and commands (default ask; `run` defaults to allow)")
    p.add_argument("-q", "--quiet", action="store_true", default=flag)
    p.add_argument("--no-color", action="store_true", default=flag)


SUBCOMMANDS = ("run", "where", "plan", "auth", "models", "session", "stats",
               "init", "config")


def split_project(argv: list) -> tuple:
    """`jevcode ../other-project` — a bare directory, the way opencode takes one.

    It cannot be an argparse positional: with subcommands present, argparse
    would happily read the word `run` as a directory name. So the first word is
    claimed here, and only when it is neither a flag nor a subcommand.
    """
    if argv and not argv[0].startswith("-") and argv[0] not in SUBCOMMANDS:
        return argv[0], argv[1:]
    return "", argv


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project, argv = split_project(argv)
    args = parser().parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__
        print(__version__)
        return 0

    root = args.directory or project or "."
    if not os.path.isdir(root):
        print("jevcode: no such directory: %s" % root, file=sys.stderr)
        return 2

    cfg = Config(root).apply(
        jev_model=args.jev_model, writer_model=args.writer_model,
        candidates=args.candidates, max_steps=args.max_steps,
        permission=args.permission)
    ui = Ui(color=False if args.no_color else None, quiet=args.quiet)
    if getattr(args, "demo", False):
        from .demo import playground
        root = playground()
        cfg = Config(root)
        cfg["demo"] = True
        cfg["permission"] = args.permission or "allow"
        ui.say(ui.paint("demo project in %s" % root, "grey"))

    table = {
        None: _interactive, "run": _run, "where": _where, "plan": _plan,
        "auth": _auth, "models": _models, "session": _session, "stats": _stats,
        "init": _init, "config": _config,
    }
    try:
        return table[args.command](args, cfg, ui, root)
    except RuntimeError as ex:
        print("jevcode: %s" % ex, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130


# --------------------------------------------------------------- the modes

def _interactive(args, cfg: Config, ui: Ui, root: str) -> int:
    from .repl import Repl
    from . import net
    net.warm(cfg["systemone_url"], cfg["writer_url"])
    resume = "last" if args.resume_last else (args.session or "")
    return Repl(root, cfg, ui, resume=resume).run()


def _run(args, cfg: Config, ui: Ui, root: str) -> int:
    """One task, no conversation. The shape scripts and CI want."""
    task = " ".join(args.message).strip()
    if not task:
        print("jevcode run <what to do>", file=sys.stderr)
        return 2
    repo = Repo(root)
    for path in args.file or []:
        task += "\n\nLook at %s." % path
    if args.permission is None and not args.dry_run:
        cfg["permission"] = "allow"        # nobody is there to answer

    from . import net
    net.warm(cfg["systemone_url"], cfg["writer_url"])
    usage, writer_usage = Usage(), WriterUsage()
    one = SystemOne(url=cfg["systemone_url"], key=key_for("systemone"),
                    model=cfg["jev_model"], usage=usage)
    writer = Writer(url=cfg["writer_url"], key=key_for("writer"),
                    model=cfg["writer_model"], usage=writer_usage)
    quiet = args.quiet or args.format == "json"
    trace = Trace(args.trace, quiet=quiet, color=ui.color and not quiet)
    agent = Agent(task, root, one=one, writer=writer, trace=trace,
                  max_steps=int(cfg["max_steps"]), candidates=int(cfg["candidates"]),
                  dry_run=args.dry_run, allow_commands=not args.no_commands,
                  permit=Permission(cfg["permission"], None if quiet else ui),
                  instructions=cfg.instructions(),
                  settle_at=int(cfg.get("settle_at") or 0),
                  settle_grace=float(cfg.get("settle_grace") or 2.5), repo=repo)

    started = time.time()
    if not quiet:
        trace.say(trace.paint(task, "bold"))
    outcome = agent.run()
    spent = time.time() - started

    ses = sessions.Session(root)
    ses.add(task, outcome.reason, agent.edits, {
        "decisions": usage.questions, "requests": usage.requests,
        "writer_calls": writer_usage.calls, "cost": usage.cost + writer_usage.cost,
        "currency": usage.currency or writer_usage.currency, "seconds": round(spent, 2)},
        steps=outcome.steps)

    if args.format == "json":
        print(json.dumps({
            "task": task, "finished": outcome.finished, "reason": outcome.reason,
            "steps": outcome.steps, "session": ses.id,
            "changed": sorted({e.path for e in outcome.edits}),
            "checks": outcome.checks[-3:],
            "decisions": usage.questions, "requests": usage.requests,
            "writer_calls": writer_usage.calls,
            "cost": round(usage.cost + writer_usage.cost, 6),
            "currency": usage.currency or writer_usage.currency,
            "seconds": round(spent, 2),
        }, ensure_ascii=False, indent=2))
    else:
        trace.summary(usage, writer_usage,
                      ("done — " if outcome.finished else "stopped — ") + outcome.reason)
    return 0 if outcome.finished else 1


def _where(args, cfg: Config, ui: Ui, root: str) -> int:
    question = " ".join(args.question).strip()
    if not question:
        print("jevcode where <what you are looking for>", file=sys.stderr)
        return 2
    usage = Usage()
    one = SystemOne(url=cfg["systemone_url"], key=key_for("systemone"),
                    model=cfg["jev_model"], usage=usage)
    repo = Repo(root)
    found = locate.pick_file(one, repo, question)
    if not found["file"]:
        print("nothing to look at in %s" % repo.root, file=sys.stderr)
        return 1
    ui.say("%s  %s" % (ui.paint(found["file"], "bold"),
                       ui.paint("p=%.2f any=%.2f" % (found["ranked"][0][1], found["any"]),
                                "grey")))
    for name, p in found["ranked"][1:4]:
        ui.say("  %s %s" % (ui.paint("%.2f" % p, "grey"), name))
    region = locate.pick_region(one, repo, found["file"], question)
    ui.say("%s  %s" % (ui.paint(region["region"].label, "cyan"),
                       ui.paint("conf=%.2f" % region["confidence"], "grey")))
    body = repo.read(found["file"]).splitlines()
    r = region["region"]
    for n in range(r.start, min(r.end, r.start + 20) + 1):
        ui.say("  %4d  %s" % (n, body[n - 1]))
    ui.say(ui.paint("%d decisions in %d requests, %.1fs"
                    % (usage.questions, usage.requests, usage.seconds), "grey"))
    return 0


def _plan(args, cfg: Config, ui: Ui, root: str) -> int:
    from . import plan as planning
    task = " ".join(args.question).strip()
    if not task:
        print("jevcode plan <what to do>", file=sys.stderr)
        return 2
    usage = Usage()
    one = SystemOne(url=cfg["systemone_url"], key=key_for("systemone"),
                    model=cfg["jev_model"], usage=usage)
    repo = Repo(root)
    state = {"task": task, "history": [],
             "repository": {"name": os.path.basename(repo.root),
                            "files": repo.files()[:120],
                            "commands": list(repo.known_commands())}}
    for branch in planning.lookahead(one, state):
        ui.say("%s  %s" % (ui.paint("%.2f" % branch.score, "cyan"), branch))
    ui.say(ui.paint("%d decisions in %d requests, %.1fs"
                    % (usage.questions, usage.requests, usage.seconds), "grey"))
    return 0


# ------------------------------------------------------------ housekeeping

def _auth(args, cfg: Config, ui: Ui, root: str) -> int:
    import getpass
    if args.action == "login":
        which = args.which or ""
        wanted = [which] if which else ["systemone", "writer"]
        for name in wanted:
            label = {"systemone": "System One key (the model that decides)",
                     "writer": "writer key (the model that writes code)"}[name]
            try:
                value = getpass.getpass("%s: " % label).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 130
            if value:
                save_credential(name, value)
        ui.good("saved in %s" % auth_file())
        return 0
    if args.action == "logout":
        for name in ([args.which] if args.which else ["systemone", "writer"]):
            save_credential(name, "")
        ui.say("removed from %s" % auth_file())
        return 0
    saved = credentials()
    for name in ("systemone", "writer"):
        where = ("environment" if key_for(name) and name not in saved
                 else "auth.json" if name in saved else "")
        ui.say("  %-10s %s" % (name, ui.paint(where or "not set",
                                              "green" if where else "red")))
    return 0


def _models(args, cfg: Config, ui: Ui, root: str) -> int:
    ui.say("  decides  %s" % ui.paint(cfg["jev_model"], "bold"))
    ui.say("           %s" % ui.paint(cfg["systemone_url"], "grey"))
    ui.say("  writes   %s" % ui.paint(cfg["writer_model"], "bold"))
    ui.say("           %s" % ui.paint(cfg["writer_url"], "grey"))
    ui.say()
    ui.say(ui.paint("  the writer is any OpenAI-compatible endpoint; small and fast "
                    "beats large here,", "grey"))
    ui.say(ui.paint("  because several drafts are written in parallel and judged.",
                    "grey"))
    return 0


def _session(args, cfg: Config, ui: Ui, root: str) -> int:
    if args.action in ("list", "ls"):
        rows = sessions.listing(root)
        if args.format == "json":
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            ui.say("no sessions in %s" % os.path.abspath(root))
            return 0
        for row in rows:
            ui.say("%s  %-10s  %2d turns  %s" % (
                ui.paint(row["id"], "cyan"), sessions.ago(row["updated"]),
                row["turns"], row["title"][:60]))
        return 0
    if not args.id:
        print("jevcode session %s <id>" % args.action, file=sys.stderr)
        return 2
    if args.action == "delete":
        os.unlink(os.path.join(sessions.store(root), args.id + ".json"))
        ui.say("deleted %s" % args.id)
        return 0
    loaded = sessions.Session.load(root, args.id)
    if args.action == "export":
        print(json.dumps({"id": loaded.id, "title": loaded.title,
                          "turns": loaded.turns}, ensure_ascii=False, indent=2))
    else:
        print(loaded.transcript())
    return 0


def _stats(args, cfg: Config, ui: Ui, root: str) -> int:
    rows = sessions.listing(root, limit=1000)
    cutoff = time.time() - args.days * 86400 if args.days else 0
    total = {"turns": 0, "decisions": 0, "requests": 0, "writer_calls": 0,
             "cost": 0.0, "currency": "", "seconds": 0.0}
    counted = 0
    for row in rows:
        if row["updated"] < cutoff:
            continue
        spent = sessions.Session.load(root, row["id"]).spent()
        counted += 1
        total["turns"] += row["turns"]
        for key in ("decisions", "requests", "writer_calls"):
            total[key] += spent[key]
        total["cost"] += spent["cost"]
        total["seconds"] += spent["seconds"]
        total["currency"] = total["currency"] or spent["currency"]
    ui.say("  %d sessions · %d turns" % (counted, total["turns"]))
    ui.say("  %d decisions in %d requests · %d drafts written"
           % (total["decisions"], total["requests"], total["writer_calls"]))
    ui.say("  %.0f seconds of agent time" % total["seconds"])
    if total["cost"]:
        ui.say("  %.4f %s" % (total["cost"], total["currency"]))
    return 0


def _init(args, cfg: Config, ui: Ui, root: str) -> int:
    repo = Repo(root)
    path = write_instructions(root, "", repo.known_commands())
    ui.good("wrote %s" % path)
    ui.say(ui.paint("  say what the project is in a sentence; every terminal agent "
                    "reads this file", "grey"))
    return 0


def _config(args, cfg: Config, ui: Ui, root: str) -> int:
    for key in sorted(cfg):
        ui.say("  %-14s %s" % (key, cfg[key]))
    ui.say()
    ui.say(ui.paint("  user:    %s" % user_file(), "grey"))
    ui.say(ui.paint("  project: %s" % os.path.join(os.path.abspath(root), ".jevcode.json"),
                    "grey"))
    if cfg.sources:
        ui.say(ui.paint("  read:    %s" % ", ".join(cfg.sources), "grey"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
