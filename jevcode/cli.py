"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys

from . import locate
from .engine import Agent
from .repo import Repo
from .systemone import SystemOne, Usage
from .trace import Trace
from .writer import Writer, WriterUsage

HELP = """jevcode — a coding agent whose decisions are made by a model that cannot write text.

  jevcode "make the parser accept a trailing comma"
  jevcode --dry-run "rename the flag --verbose to --loud"
  jevcode where "the retry backoff"

Keys:
  TYPESAFE_API_KEY        System One key (https://console.typesafe.ai/keys)
  JEVCODE_WRITER_KEY      key for the model that writes code
  JEVCODE_WRITER_URL      its OpenAI-compatible endpoint
  JEVCODE_WRITER_MODEL    which model writes (small and fast beats large here)
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevcode", description=HELP,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("task", nargs="*", help="what to do, in plain words")
    p.add_argument("-C", "--directory", default=".", help="repository to work in")
    p.add_argument("-n", "--candidates", type=int, default=6,
                   help="how many drafts the writer produces per edit (default 6)")
    p.add_argument("--max-steps", type=int, default=24)
    p.add_argument("--dry-run", action="store_true",
                   help="show the change it would make, change nothing")
    p.add_argument("--no-commands", action="store_true",
                   help="never run anything, not even the tests")
    p.add_argument("--trace", metavar="FILE", help="write every decision to a jsonl file")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--jev-model", default=None)
    p.add_argument("--writer-model", default=None)
    p.add_argument("--version", action="store_true")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    words = list(args.task)
    if words and words[0] == "where":
        return _where(args, " ".join(words[1:]))
    if words and words[0] == "plan":
        return _plan(args, " ".join(words[1:]))
    task = " ".join(words).strip()
    if not task:
        parser().print_help()
        return 2

    usage, writer_usage = Usage(), WriterUsage()
    one = SystemOne(model=args.jev_model, usage=usage)
    writer = Writer(model=args.writer_model, usage=writer_usage)
    trace = Trace(args.trace, quiet=args.quiet)
    agent = Agent(task, args.directory, one=one, writer=writer, trace=trace,
                  max_steps=args.max_steps, candidates=args.candidates,
                  dry_run=args.dry_run, allow_commands=not args.no_commands)
    trace.say(trace.paint(task, "bold"))
    try:
        outcome = agent.run()
    except KeyboardInterrupt:
        trace.say("\ninterrupted")
        return 130
    except RuntimeError as ex:
        print("jevcode: %s" % ex, file=sys.stderr)
        return 1
    trace.summary(usage, writer_usage,
                  ("done — " if outcome.finished else "stopped — ") + outcome.reason)
    return 0 if outcome.finished else 1


def _plan(args, task: str) -> int:
    """Show the beam of likely action sequences without doing any of them."""
    if not task:
        print("jevcode plan <what to do>", file=sys.stderr)
        return 2
    from . import plan as planning
    usage = Usage()
    one = SystemOne(model=args.jev_model, usage=usage)
    repo = Repo(args.directory)
    trace = Trace(args.trace, quiet=args.quiet)
    state = {"task": task, "history": [],
             "repository": {"name": os.path.basename(repo.root),
                            "files": repo.files()[:120],
                            "commands": list(repo.known_commands())}}
    branches = planning.lookahead(one, state)
    for branch in branches:
        trace.say("%s  %s" % (trace.paint("%.2f" % branch.score, "cyan"), branch))
    trace.say(trace.paint("%d questions in %d requests, %.1fs"
                          % (usage.questions, usage.requests, usage.seconds), "dim"))
    return 0


def _where(args, question: str) -> int:
    """Locate something in the repository. One request for the file, one for the region."""
    if not question:
        print("jevcode where <what you are looking for>", file=sys.stderr)
        return 2
    usage = Usage()
    one = SystemOne(model=args.jev_model, usage=usage)
    repo = Repo(args.directory)
    trace = Trace(args.trace, quiet=args.quiet)
    found = locate.pick_file(one, repo, question)
    if not found["file"]:
        print("nothing to look at in %s" % repo.root, file=sys.stderr)
        return 1
    trace.say("%s  %s" % (trace.paint(found["file"], "bold"),
                          trace.paint("p=%.2f any=%.2f" % (found["ranked"][0][1], found["any"]),
                                      "dim")))
    for name, p in found["ranked"][1:4]:
        trace.say("  %s %s" % (trace.paint("%.2f" % p, "dim"), name))
    region = locate.pick_region(one, repo, found["file"], question)
    trace.say("%s  %s" % (trace.paint(region["region"].label, "cyan"),
                          trace.paint("conf=%.2f" % region["confidence"], "dim")))
    body = repo.read(found["file"]).splitlines()
    r = region["region"]
    for n in range(r.start, min(r.end, r.start + 20) + 1):
        trace.say("  %4d  %s" % (n, body[n - 1]))
    trace.say(trace.paint("%d questions in %d requests, %.1fs"
                          % (usage.questions, usage.requests, usage.seconds), "dim"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
