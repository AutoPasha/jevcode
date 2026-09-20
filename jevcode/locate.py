"""Finding the place to change, without an index.

The usual answer is embeddings: chunk the repository, store vectors, keep them
fresh, retrieve by cosine distance. That machinery exists because asking a
language model about every file is too slow and too expensive. With System One
it is neither — two hundred files can be judged in one request, so the index
disappears and grep plus the model's own judgement take its place.

Two steps, two requests: which file, then which region of it.
"""

from __future__ import annotations

import os
import re
import subprocess

from . import questions
from .systemone import MAX_OPTIONS

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
QUOTED_RE = re.compile(r"[\"'`]([^\"'`\n]{2,60})[\"'`]")
PATHY_RE = re.compile(r"[\w./-]+\.[A-Za-z]{1,5}")

STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "into", "when", "make",
    "add", "fix", "use", "not", "but", "you", "are", "was", "has", "have", "its",
    "should", "would", "can", "code", "file", "files", "function", "method", "test",
    "tests", "please", "also", "then", "than", "them", "they", "there", "where",
    "which", "while", "after", "before", "about", "does", "did", "done", "new",
}


def search_terms(task: str, limit: int = 12) -> list:
    """Literal strings worth grepping for, pulled out of the task in code.

    The model never invents a search string: it only picks among strings that
    actually occur in the task. A closed set again — the same trick as
    everywhere else in this agent.
    """
    out = []
    for m in QUOTED_RE.finditer(task):
        out.append(m.group(1).strip())
    for m in PATHY_RE.finditer(task):
        out.append(m.group(0))
    for m in WORD_RE.finditer(task):
        word = m.group(0)
        if word.lower() in STOPWORDS:
            continue
        if word.islower() and len(word) < 4:
            continue
        out.append(word)
    seen, keep = set(), []
    for term in out:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        keep.append(term)
    return keep[:limit]


def grep(root: str, term: str, limit: int = 60) -> list:
    """Files containing a literal string. ripgrep if present, git grep otherwise."""
    for argv in (["rg", "-l", "--fixed-strings", "--", term, "."],
                 ["git", "grep", "-l", "--fixed-strings", "--", term]):
        try:
            r = subprocess.run(argv, cwd=root, capture_output=True, timeout=25)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode not in (0, 1):
            continue
        found = [line.strip().lstrip("./")
                 for line in r.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
        return found[:limit]
    return []


def shortlist(repo, task: str, limit: int = MAX_OPTIONS) -> dict:
    """Up to 255 files worth considering, with a one-line reason each.

    Files that grep found for a term from the task come first, then the rest of
    the tree, so a small repository is simply handed over whole.
    """
    hits: dict = {}
    for term in search_terms(task):
        for rel in grep(repo.root, term):
            hits.setdefault(rel, []).append(term)
    ordered = [rel for rel, _ in sorted(hits.items(), key=lambda kv: -len(kv[1]))]
    ordered += [rel for rel in repo.files() if rel not in hits]
    rich = len(ordered) <= 150

    options: dict = {}
    for rel in ordered[:limit]:
        entry = {"about": _about(repo.root, rel)}
        if rel in hits:
            entry["mentions"] = sorted(set(hits[rel]))[:6]
        if rich:
            names = _defines(repo, rel)
            if names:
                entry["defines"] = names
        options[rel] = entry
    return _trimmed(options)


# A question and the state it is judged against share a 32k token budget, so a
# shortlist of 255 richly described files has to know when to say less.
BUDGET_CHARS = 60_000


def _trimmed(options: dict) -> dict:
    import json
    for drop in ("defines", "about"):
        if len(json.dumps(options, ensure_ascii=False)) <= BUDGET_CHARS:
            break
        for entry in options.values():
            if drop == "about" and "about" in entry:
                entry["about"] = entry["about"][:60]
            else:
                entry.pop(drop, None)
    return options


def _about(root: str, rel: str) -> str:
    """A line the model can judge a file by, without opening it."""
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(14)]
    except OSError:
        return os.path.basename(rel)
    picked = []
    for line in head:
        line = line.strip().strip("#/*\"' ")
        if len(line) > 12 and not line.startswith(("import ", "from ", "package ", "use ")):
            picked.append(line)
        if sum(len(p) for p in picked) > 180:
            break
    return " ".join(picked)[:220] or os.path.basename(rel)


def _defines(repo, rel: str) -> list:
    """The names a file declares — often the whole answer to "which file"."""
    try:
        return [r.name for r in repo.regions(rel)
                if r.kind in ("function", "class", "block")
                and not r.name.startswith("<")][:10]
    except (OSError, ValueError):
        return []


def pick_file(one, repo, task: str, options: dict | None = None) -> dict:
    """Which file the task is about. Returns the pick, the runners-up and the gate."""
    options = options if options is not None else shortlist(repo, task)
    if not options:
        return {"file": None, "any": 0.0, "confidence": 0.0, "ranked": []}
    state = {"task": task, "candidates": options,
             "repository": os.path.basename(repo.root)}
    a = one.ask(state, questions.locate_file(options, task))
    return {"file": a.pick("file"), "any": a.p("any"),
            "confidence": a.confidence("file"),
            "ranked": a.ranked("file")[:5]}


def pick_region(one, repo, rel: str, task: str) -> dict:
    """Which region of a file the change belongs in."""
    regions = repo.regions(rel)
    if len(regions) > MAX_OPTIONS:
        regions = regions[:MAX_OPTIONS]
    if len(regions) == 1:
        return {"region": regions[0], "confidence": 1.0, "ranked": []}
    labels = {}
    body = repo.read(rel).splitlines()
    for r in regions:
        head = next((line.strip() for line in body[r.start - 1:r.end] if line.strip()), "")
        labels[r.label] = ("%s — %s" % (r.kind, head[:120])).strip(" —")
    state = {"task": task, "file": rel, "regions": labels}
    a = one.ask(state, {"region": questions.choice({
        "question": "Which region of `file` does `task` require changing?",
        "task": task,
    }, labels)})
    chosen = next(r for r in regions if r.label == a.pick("region"))
    return {"region": chosen, "confidence": a.confidence("region"),
            "ranked": a.ranked("region")[:5]}


def pick_line(one, repo, rel: str, task: str, window: int = MAX_OPTIONS) -> dict:
    """Which single line the task points at. Long files are sliced, never counted."""
    total = len(repo.read(rel).splitlines())
    best = {"line": None, "p": -1.0, "present": 0.0}
    for start in range(1, total + 1, window):
        end = min(start + window - 1, total)
        state = repo.numbered(rel, start, end)
        ids = {"L%03d" % n: None for n in range(start, end + 1)}
        a = one.ask(state, {
            "line": questions.choice({
                "question": "Which line of the file is the one `task` is about?",
                "task": task,
            }, ids),
            "present": questions.noul({
                "question": "Is the code `task` is about present in this part of the file?",
                "task": task,
            }),
        })
        top, p = a.ranked("line")[0]
        if p > best["p"]:
            best = {"line": int(top[1:]), "p": p, "present": a.p("present"),
                    "confidence": a.confidence("line")}
    return best


# --------------------------------------------------------- files that do not exist yet

NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def new_file_options(repo, task: str, limit: int = 60) -> dict:
    """Plausible paths for a file the repository does not have yet.

    The model is never asked to type a path — it picks one. So the paths are
    built here, out of two things that are already true: paths the task names
    literally, and the shape of the tree (its directories and the extension it
    mostly uses). A closed set again, and a short one.
    """
    existing = set(repo.files())
    options: dict = {}

    for match in PATHY_RE.finditer(task):
        rel = match.group(0).strip(".,;:()[]'\"").lstrip("./")
        if rel and rel not in existing and "." in os.path.basename(rel):
            options[rel] = {"why": "named in the task"}

    suffix = _common_suffix(existing)
    stems = [w.lower() for w in NAME_RE.findall(task)
             if w.lower() not in STOPWORDS][:4]
    folders = _folders(existing)
    for stem in stems:
        for folder in folders:
            rel = os.path.join(folder, stem + suffix) if folder else stem + suffix
            if rel in existing or rel in options:
                continue
            options[rel] = {"why": "a new %s file named after the task, beside %s"
                                   % (suffix.lstrip(".") or "text", folder or "the root")}
            if len(options) >= limit:
                return options
    return options


def _common_suffix(files) -> str:
    counts: dict = {}
    for rel in files:
        suffix = os.path.splitext(rel)[1].lower()
        if suffix in (".py", ".js", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".php"):
            counts[suffix] = counts.get(suffix, 0) + 1
    return max(counts, key=counts.get) if counts else ".txt"


def _folders(files, limit: int = 8) -> list:
    counts: dict = {}
    for rel in files:
        folder = os.path.dirname(rel)
        counts[folder] = counts.get(folder, 0) + 1
    ordered = sorted(counts, key=lambda f: (f.count(os.sep), -counts[f]))
    return ordered[:limit]
