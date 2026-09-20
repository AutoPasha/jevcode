"""A tiny report command."""

import argparse


def numbers(rows):
    return {"count": len(rows), "total": sum(rows),
            "average": (sum(rows) / len(rows)) if rows else 0}


def render(stats):
    return "\n".join("%s: %s" % (name, value) for name, value in stats.items())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="report")
    ap.add_argument("numbers", nargs="*", type=float)
    args = ap.parse_args(argv)
    print(render(numbers(args.numbers)))
    return 0
