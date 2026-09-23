"""Doing things: running commands, replacing regions, checking the result.

Everything here is ordinary Python. The model decides *what* and the code does
it — which also means every effect the agent has on a machine is visible in one
short file, and can be read by someone deciding whether to trust it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field


@dataclass
class Run:
    command: str
    code: int
    output: str
    seconds: float
    timed_out: bool = False
    abandoned: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and not self.abandoned


def run(command: str, cwd: str, timeout: int = 300, env: dict | None = None,
        stop=None) -> Run:
    """Run one command and report what it said.

    `stop` is an event the caller can set to mean "the answer is already known,
    this run no longer matters". Once another candidate has turned the suite
    green, the runs still going are spending cores on a question nobody will
    read, and a writer's infinite loop would otherwise hold them until the
    timeout. So the process gets its own session and the whole group is killed,
    not just the shell that fronts it.
    """
    import time
    started = time.time()
    if stop is None:
        try:
            r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                               timeout=timeout, env={**os.environ, **(env or {})})
        except subprocess.TimeoutExpired:
            return Run(command, 124, "timed out after %ds" % timeout,
                       time.time() - started, True)
        except OSError as ex:
            return Run(command, 127, str(ex), time.time() - started)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        return Run(command, r.returncode, out, round(time.time() - started, 2))
    return _run_stoppable(command, cwd, timeout, env, stop, started)


def _run_stoppable(command: str, cwd: str, timeout: int, env: dict | None,
                   stop, started: float) -> Run:
    import time
    try:
        proc = subprocess.Popen(command, shell=True, cwd=cwd,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, **(env or {})},
                                start_new_session=True)
    except OSError as ex:
        return Run(command, 127, str(ex), time.time() - started)
    # The output is drained by a thread of its own: a suite that prints more
    # than a pipe holds would otherwise block forever on a full buffer, and the
    # poll loop below would never see it finish.
    chunks: list = []
    reader = threading.Thread(target=_drain, args=(proc.stdout, chunks), daemon=True)
    reader.start()
    while True:
        if proc.poll() is not None:
            break
        if stop.is_set():
            _kill_group(proc)
            reader.join(timeout=2)
            return Run(command, -1, "abandoned: another candidate already passed",
                       round(time.time() - started, 2), abandoned=True)
        if time.time() - started > timeout:
            _kill_group(proc)
            reader.join(timeout=2)
            return Run(command, 124, "timed out after %ds" % timeout,
                       time.time() - started, True)
        time.sleep(0.05)
    reader.join(timeout=5)
    out = b"".join(chunks).decode("utf-8", "replace")
    return Run(command, proc.returncode, out, round(time.time() - started, 2))


def _drain(stream, chunks: list) -> None:
    if stream is None:
        return
    try:
        for chunk in iter(lambda: stream.read(65536), b""):
            chunks.append(chunk)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _kill_group(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:                                    # noqa: BLE001 - best effort
        pass


def replace_region(text: str, start: int, end: int, replacement: str) -> str:
    """Swap lines [start, end] (1-based, inclusive) for a block of new text."""
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:start - 1])
    tail = "".join(lines[end:])
    body = replacement.rstrip("\n") + "\n"
    if head and not head.endswith("\n"):
        head += "\n"
    return head + body + tail


@dataclass
class Edit:
    """One applied change, with everything needed to undo it."""
    path: str
    start: int
    end: int
    before: str
    after: str

    def undo(self, repo) -> None:
        repo.write(self.path, self.before)


def apply_edit(repo, rel: str, start: int, end: int, replacement: str) -> Edit:
    before = repo.read(rel)
    after = replace_region(before, start, end, replacement)
    repo.write(rel, after)
    return Edit(rel, start, end, before, after)


def create_file(repo, rel: str, text: str) -> Edit:
    exists = os.path.exists(os.path.join(repo.root, rel))
    before = repo.read(rel) if exists else ""
    repo.write(rel, text if text.endswith("\n") else text + "\n")
    return Edit(rel, 1, len(before.splitlines()) or 1, before, repo.read(rel))


# ------------------------------------------------------------ cheap checks

def syntax_error(rel: str, text: str) -> str:
    """A syntax check for the languages where one is a subprocess away.

    This runs before the model is asked to judge anything: a candidate that does
    not parse is not a matter of opinion, and filtering it in code keeps the
    judgement for candidates that actually differ in behaviour.
    """
    suffix = os.path.splitext(rel)[1].lower()
    if suffix == ".py":
        try:
            compile(text, rel, "exec")
        except SyntaxError as ex:
            return "%s at line %s" % (ex.msg, ex.lineno)
        return ""
    if suffix in (".json",):
        try:
            json.loads(text)
        except ValueError as ex:
            return str(ex)[:120]
        return ""
    if suffix in (".js", ".mjs", ".cjs") and shutil.which("node"):
        return _check_with(["node", "--check"], rel, text)
    if suffix in (".ts", ".tsx") and shutil.which("tsc"):
        return _check_with(["tsc", "--noEmit", "--skipLibCheck"], rel, text)
    if suffix in (".html", ".htm", ".css", ".scss"):
        from . import markup
        return markup.syntax(rel, text)
    return ""


def _check_with(argv: list, rel: str, text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=os.path.splitext(rel)[1],
                                     delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        r = subprocess.run(argv + [path], capture_output=True, timeout=60)
        if r.returncode != 0:
            return (r.stdout + r.stderr).decode("utf-8", "replace")[:200]
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        os.unlink(path)


def diff(before: str, after: str, rel: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/" + rel, tofile="b/" + rel, n=2))
