"""The check a page has when a page has no test suite.

A repository with an `index.html` and a stylesheet declares no commands: no
pytest, no npm script, no Makefile. The agent's only notion of "done" was a
green run of the project's own command, so on a landing page it could never be
done — it wrote the page, could not confirm it, and wrote it again until the
step budget ran out.

There is no test here to run, but there are facts to establish, and they are
the ones a person checks first: does the markup parse, is every tag closed,
and does every file the page asks the browser to load actually exist. None of
that is an opinion and none of it costs a model call.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser

# Elements that never have a closing tag; an unclosed `<br>` is not a mistake.
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
# Elements whose closing tag the HTML spec lets you leave out.
OPTIONAL_END = {"li", "dt", "dd", "p", "option", "thead", "tbody", "tfoot",
                "tr", "td", "th", "rt", "rp", "head", "body", "html"}

LOCAL_REF = re.compile(
    r"""(?:href|src)\s*=\s*["']([^"'#?]+)""", re.I)
# A url() inside a stylesheet points at a file the same way an attribute does.
CSS_REF = re.compile(r"""url\(\s*["']?([^"')#?]+)""", re.I)


class _Tags(HTMLParser):
    """A stack of open tags, and the ones that never got closed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.stray: list = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return
        self.stray.append((tag, self.getpos()[0]))


def syntax(rel: str, text: str) -> str:
    """A candidate's own problems, before it is written anywhere.

    Only what can be judged from the text itself: the file system question —
    whether `style.css` exists — belongs to `problems`, which runs after the
    file is in place and can see the rest of the tree.
    """
    suffix = os.path.splitext(rel)[1].lower()
    if suffix in (".html", ".htm"):
        parser = _Tags()
        try:
            parser.feed(text)
            parser.close()
        except Exception as ex:                     # a parser that gives up
            return str(ex)[:120]
        unclosed = [t for t in parser.stack if t[0] not in OPTIONAL_END]
        if unclosed:
            tag, line = unclosed[0]
            return "<%s> at line %d is never closed" % (tag, line)
        if parser.stray:
            tag, line = parser.stray[0]
            return "</%s> at line %d closes a tag that was never opened" % (tag, line)
        return ""
    if suffix in (".css", ".scss"):
        depth, line = 0, 0
        for n, row in enumerate(text.splitlines(), 1):
            row = re.sub(r"/\*.*?\*/", "", row)
            for ch in row:
                if ch == "{":
                    depth += 1
                    line = line or n
                elif ch == "}":
                    depth -= 1
                    if depth < 0:
                        return "a closing brace at line %d has no rule to close" % n
        if depth > 0:
            return "a rule opened at line %d is never closed" % line
        return ""
    return ""


def watched(rel: str) -> bool:
    """Whether this file is one the page check knows how to judge."""
    return os.path.splitext(rel)[1].lower() in (".html", ".htm", ".css", ".scss")


def problems(repo, rel: str) -> list:
    """What is wrong with the page as it now stands in the tree.

    Returns plain sentences, because they go straight into the agent's history
    and the model reads them there.
    """
    suffix = os.path.splitext(rel)[1].lower()
    if suffix not in (".html", ".htm", ".css", ".scss"):
        return []
    try:
        text = repo.read(rel)
    except OSError:
        return []
    found = []
    bad = syntax(rel, text)
    if bad:
        found.append(bad)
    pattern = CSS_REF if suffix in (".css", ".scss") else LOCAL_REF
    base = os.path.dirname(rel)
    seen = set()
    for ref in pattern.findall(text):
        ref = ref.strip()
        if (not ref or ref in seen or "://" in ref or ref.startswith("//")
                or ref.startswith("data:") or ref.startswith("mailto:")
                or ref.startswith("tel:") or ref.startswith("/")):
            continue
        seen.add(ref)
        target = os.path.normpath(os.path.join(base, ref))
        if target.startswith(".."):
            continue
        if not os.path.exists(os.path.join(repo.root, target)):
            found.append("%s links to %s, and that file does not exist" % (rel, ref))
    return found


CLASS_ATTR = re.compile(r"""class\s*=\s*["']([^"']+)["']""", re.I)
IMG_SRC = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)""", re.I)


def remote_pictures(repo, rel: str) -> list:
    """Pictures the page can only show if someone else's server is up.

    Not an error in the markup, and said so: a fact about what the page will
    look like on a machine with no network, or in a year when the image host
    has gone. Both happened in the first live run — a hero from unsplash and
    three icons from via.placeholder.com, a service that no longer resolves.
    """
    try:
        text = repo.read(rel)
    except OSError:
        return []
    suffix = os.path.splitext(rel)[1].lower()
    if suffix in (".css", ".scss"):
        refs = CSS_REF.findall(text)
    elif suffix in (".html", ".htm"):
        refs = IMG_SRC.findall(text) + CSS_REF.findall(text)
    else:
        return []
    out = []
    for ref in refs:
        ref = ref.strip()
        if (ref.startswith(("http://", "https://", "//"))
                and ref not in out):
            out.append(ref)
    return out


def unstyled(repo, rel: str) -> list:
    """Classes the page uses that none of its stylesheets mention.

    Softer than a problem and kept apart from one: a page whose footer has no
    rules still renders, it just renders as a bulleted list of blue links —
    which is exactly what the first live run produced. The agent gets told; it
    is not blocked from finishing over it.
    """
    if os.path.splitext(rel)[1].lower() not in (".html", ".htm"):
        return []
    try:
        text = repo.read(rel)
    except OSError:
        return []
    sheets = []
    base = os.path.dirname(rel)
    for ref in LOCAL_REF.findall(text):
        if not ref.lower().endswith((".css", ".scss")):
            continue
        target = os.path.normpath(os.path.join(base, ref.strip()))
        try:
            sheets.append(repo.read(target))
        except OSError:
            continue
    if not sheets:                       # nothing to be missing from
        return []
    styles = "\n".join(sheets) + "\n" + "\n".join(
        re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
    missing = []
    for attr in CLASS_ATTR.findall(text):
        for name in attr.split():
            if name not in missing and ("." + name) not in styles:
                missing.append(name)
    return missing


def describe(rel: str, found: list) -> str:
    if not found:
        return "checked %s: the markup parses and every file it links to exists" % rel
    return "checked %s: %s" % (rel, "; ".join(found[:3]))
