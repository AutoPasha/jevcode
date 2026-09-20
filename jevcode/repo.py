"""The repository as a closed set of options.

Everything Jev decides has to be a closed set, so this module turns a working
tree into sets: files, regions of a file, anchors inside a file, commands the
project knows how to run. No embeddings, no vector store, no index to keep
fresh — `git ls-files` and the file itself are the index, and the model does
the judging.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             ".next", ".cache", "target", ".mypy_cache", ".pytest_cache", ".tox"}
TEXT_SUFFIX = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
               ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".kt", ".sh", ".bash",
               ".sql", ".html", ".css", ".scss", ".vue", ".svelte", ".md", ".rst", ".txt",
               ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env.example"}
MAX_FILE_BYTES = 400_000


@dataclass
class Region:
    """A named, addressable slice of a file — what an edit is applied to."""
    name: str
    start: int           # 1-based, inclusive
    end: int             # 1-based, inclusive
    kind: str = "block"  # function | class | block | file

    @property
    def label(self) -> str:
        return "%s (lines %d-%d)" % (self.name, self.start, self.end)


class Repo:
    def __init__(self, root: str = "."):
        self.root = os.path.abspath(root)
        self._files: list | None = None
        self._cache: dict = {}

    # ---------------------------------------------------------------- files

    def files(self) -> list:
        if self._files is None:
            self._files = self._list_files()
        return self._files

    def _list_files(self) -> list:
        out = []
        tracked = self._git(["ls-files", "-z"])
        if tracked is not None:
            out = [p for p in tracked.split("\0") if p]
        else:
            for base, dirs, names in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in names:
                    rel = os.path.relpath(os.path.join(base, name), self.root)
                    out.append(rel)
        keep = []
        for rel in out:
            suffix = os.path.splitext(rel)[1].lower()
            if suffix and suffix not in TEXT_SUFFIX:
                continue
            full = os.path.join(self.root, rel)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            keep.append(rel)
        keep.sort()
        return keep

    def _git(self, args: list) -> str | None:
        try:
            r = subprocess.run(["git", "-C", self.root] + args,
                               capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", "replace")

    # ----------------------------------------------------------------- read

    def read(self, rel: str) -> str:
        if rel not in self._cache:
            with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
                self._cache[rel] = fh.read()
        return self._cache[rel]

    def write(self, rel: str, text: str) -> None:
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        self._cache[rel] = text

    def forget(self, rel: str | None = None) -> None:
        if rel is None:
            self._cache.clear()
        else:
            self._cache.pop(rel, None)

    def numbered(self, rel: str, start: int = 1, end: int | None = None) -> str:
        """Lines prefixed with stable ids, the shape a Choice over lines needs."""
        lines = self.read(rel).splitlines()
        end = len(lines) if end is None else min(end, len(lines))
        width = max(3, len(str(end)))
        return "\n".join("L%0*d| %s" % (width, n, lines[n - 1])
                         for n in range(max(start, 1), end + 1))

    # -------------------------------------------------------------- regions

    def regions(self, rel: str, max_lines: int = 120) -> list:
        """Named slices of a file: defs and classes where we can parse them,
        fixed windows where we cannot. This is the option set an edit picks from."""
        text = self.read(rel)
        lines = text.splitlines()
        if not lines:
            return [Region(os.path.basename(rel), 1, 1, "file")]
        found = _python_regions(text) if rel.endswith(".py") else _brace_regions(lines)
        found = [r for r in found if r.end - r.start + 1 <= max_lines * 3]
        if not found:
            found = _window_regions(len(lines), max_lines)
        found.sort(key=lambda r: r.start)
        return found

    # ------------------------------------------------------------ unwritten

    def stubs(self, rel: str) -> list:
        """Regions of a file that are declared but not written yet.

        A signature with `pass` under it is not code to be edited, it is a
        blank to be filled, and the difference decides how the change is
        scoped. A file with two blanks cannot be made to pass its tests one
        blank at a time, so the agent needs to know this before it writes
        rather than after the suite has rejected two correct half-changes.
        """
        lines = self.read(rel).splitlines()
        code = [r for r in self.regions(rel) if r.kind in ("function", "class")]
        out = []
        for region in code:
            if _is_stub(lines[region.start - 1:region.end]):
                out.append(region)
            elif region.kind == "class" and _hollow(region, code, lines):
                # A class is not written just because it has methods: if every
                # one of them is a blank, the class itself is a blank. Counting
                # it as written is how `class Robot: def __init__: pass` — the
                # commonest shape of a task that says "write this class" — used
                # to be patched method by method instead of authored.
                out.append(region)
        return out

    def greenfield(self, rel: str) -> bool:
        """Is this file mostly blanks — something to write rather than to edit?

        Counted over the innermost regions only — the methods and functions
        that actually hold code. A hollow class and each of its empty methods
        are the same blank seen at two depths, and counting both would let one
        empty class outvote everything written around it.
        """
        code = [r for r in self.regions(rel) if r.kind in ("function", "class")]
        if not code:
            return not self.read(rel).strip()
        leaves = [r for r in code if not any(o is not r and r.start <= o.start
                                             and o.end <= r.end for o in code)]
        blanks = [r for r in self.stubs(rel) if r in leaves]
        return len(blanks) >= 2 or len(blanks) == len(leaves)

    # ------------------------------------------------------------- commands

    def known_commands(self) -> dict:
        """Commands the project itself declares. A closed set the agent picks from,
        so `run` never needs the model to invent a shell line."""
        cmds: dict = {}
        join = lambda *p: os.path.join(self.root, *p)          # noqa: E731
        python_project = (os.path.exists(join("pyproject.toml"))
                          or os.path.exists(join("setup.py"))
                          or os.path.exists(join("pytest.ini"))
                          or _python_tests(join("tests")))
        if python_project and _pytest_command():
            cmds[_pytest_command()] = "run the Python test suite"
        elif python_project and os.path.isdir(join("tests")):
            cmds["python3 -m unittest discover -s tests -q"] = "run the Python test suite"
        if os.path.exists(join("Makefile")):
            for target in _make_targets(join("Makefile")):
                cmds["make " + target] = "makefile target `%s`" % target
        pkg = join("package.json")
        if os.path.exists(pkg):
            for name in _npm_scripts(pkg):
                cmds["npm run " + name] = "npm script `%s`" % name
        if os.path.exists(join("Cargo.toml")):
            cmds["cargo test"] = "run the Rust test suite"
        if os.path.exists(join("go.mod")):
            cmds["go test ./..."] = "run the Go test suite"
        return cmds


# --------------------------------------------------------------- internals

def _python_tests(directory: str) -> bool:
    """A `tests/` directory is not by itself a Python project.

    The JavaScript task in our own benchmark keeps its tests there too, and
    calling that project Python put `python3 -m pytest -q` at the head of the
    command list. Pytest then ran, collected nothing, and failed identically
    for every candidate — so a correct change looked exactly like a wrong one
    and the agent gave up on a task it used to pass.
    """
    try:
        return any(f.endswith(".py") for f in os.listdir(directory))
    except OSError:
        return False


def _pytest_command() -> str:
    """Only offer a command the machine can actually run, in the form that runs.

    A missing test runner fails exactly like a broken patch, and an agent that
    cannot tell the two apart will happily throw away a correct change. Cheaper
    to check once here than to reason about it later — and `pytest` being
    importable does not mean there is a `pytest` on PATH, which is the usual
    shape of an installation that is not on the system path.
    """
    import importlib.util
    import shutil
    if shutil.which("pytest"):
        return "pytest -q"
    if importlib.util.find_spec("pytest"):
        return "python3 -m pytest -q"
    return ""


DEF_RE = re.compile(r"^(\s*)(?:async\s+)?(def|class)\s+([A-Za-z_][\w]*)")


def _python_regions(text: str) -> list:
    """Top-level defs and classes with their decorators, plus methods of classes."""
    try:
        import ast
        tree = ast.parse(text)
    except SyntaxError:
        return _brace_regions(text.splitlines())
    out = []

    def visit(node, prefix=""):
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                end = getattr(child, "end_lineno", child.lineno)
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                name = prefix + child.name
                out.append(Region(name, start, end, kind))
                if isinstance(child, ast.ClassDef):
                    visit(child, name + ".")

    visit(tree)
    if out:
        head_end = min(r.start for r in out) - 1
        if head_end >= 1:
            out.append(Region("<module header>", 1, head_end, "block"))
    return out


BRACE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|var|def|fn|func|type|interface|impl|struct)\s+"
    r"([A-Za-z_$][\w$]*)")


def _brace_regions(lines: list) -> list:
    """Cheap language-agnostic split: a new top-level declaration starts a region."""
    starts = []
    for i, line in enumerate(lines, 1):
        if line[:1].strip() and BRACE_RE.match(line):
            m = BRACE_RE.match(line)
            starts.append((i, m.group(1)))
    if not starts:
        return []
    out = []
    if starts[0][0] > 1:
        out.append(Region("<file header>", 1, starts[0][0] - 1, "block"))
    for idx, (line_no, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        out.append(Region(name, line_no, end, "block"))
    return out


def _window_regions(total: int, size: int) -> list:
    out = []
    for start in range(1, total + 1, size):
        end = min(start + size - 1, total)
        out.append(Region("lines %d-%d" % (start, end), start, end, "block"))
    return out


STUB_BODY = re.compile(
    r"^(pass|\.\.\.|return|return None|return NotImplemented"
    r"|raise NotImplementedError.*|todo!\(\)|unimplemented!\(\)"
    r"|throw new Error\(.*\)|panic\(.*\))$", re.I)

DOC_MARKS = ('"""', "'''")


def _hollow(region, regions: list, lines: list) -> bool:
    """A class whose every method is a blank, and which holds nothing else.

    The methods are cut out of the body and what is left — the declaration, a
    docstring, maybe a constant — is put through the same test as any other
    stub. If nothing is left but signatures over `pass`, there is no code here
    to patch, only a class to write.
    """
    inside = [r for r in regions
              if r is not region and region.start <= r.start and r.end <= region.end]
    if not inside:
        return False
    if not all(_is_stub(lines[r.start - 1:r.end]) for r in inside):
        return False
    covered = {n for r in inside for n in range(r.start, r.end + 1)}
    rest = [lines[n - 1] for n in range(region.start, region.end + 1) if n not in covered]
    return _is_stub(rest)


def _is_stub(lines: list) -> bool:
    """Everything under the signature is a placeholder, a comment or a docstring."""
    if not lines:
        return True
    body, open_mark = [], ""
    for raw in lines[1:]:
        line = raw.strip()
        if not line:
            continue
        if open_mark:
            if open_mark in line:
                open_mark = ""
            continue
        mark = next((m for m in DOC_MARKS if line.startswith(m)), "")
        if mark:
            if not (line.endswith(mark) and len(line) > len(mark) * 2 - 1):
                open_mark = mark
            continue
        if line.startswith(("#", "//", "/*", "*")):
            continue
        body.append(line.rstrip(";").rstrip("{}").strip())
    return all(STUB_BODY.match(line) for line in body if line)


def _make_targets(path: str) -> list:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
                if m and m.group(1) not in out:
                    out.append(m.group(1))
    except OSError:
        return []
    return out[:20]


def _npm_scripts(path: str) -> list:
    import json
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return list((data.get("scripts") or {}))[:20]
