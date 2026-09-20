"""Conversations that survive the terminal being closed.

A session is a list of turns, and a turn remembers not only what was said but
every file the agent touched, with the text before and after. That is what
makes `/undo` honest: the change is put back from what was actually on disk,
not reconstructed from a diff and not delegated to git — which matters,
because plenty of the places this runs are not a repository at all.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

from .config import data_dir


def _slug(path: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.basename(os.path.abspath(path)) or "root")
    return "%s-%08x" % (name.strip("-").lower() or "root",
                        abs(hash(os.path.abspath(path))) % 0xFFFFFFFF)


def store(root: str) -> str:
    path = os.path.join(data_dir(), "sessions", _slug(root))
    os.makedirs(path, exist_ok=True)
    return path


class Turn(dict):
    @property
    def edits(self) -> list:
        return self.get("edits") or []


class Session:
    def __init__(self, root: str, ident: str = "", title: str = ""):
        self.root = os.path.abspath(root)
        self.id = ident or uuid.uuid4().hex[:12]
        self.title = title
        self.created = time.time()
        self.updated = self.created
        self.turns: list = []
        self.undone: list = []          # turns rolled back, waiting for /redo

    # ------------------------------------------------------------- on disk

    @property
    def path(self) -> str:
        return os.path.join(store(self.root), self.id + ".json")

    def save(self) -> str:
        self.updated = time.time()
        payload = {"id": self.id, "root": self.root, "title": self.title,
                   "created": self.created, "updated": self.updated,
                   "turns": self.turns, "undone": self.undone}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self.path)
        return self.path

    @classmethod
    def load(cls, root: str, ident: str) -> "Session":
        path = os.path.join(store(root), ident + ".json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        s = cls(data.get("root") or root, data["id"], data.get("title", ""))
        s.created = data.get("created", time.time())
        s.updated = data.get("updated", s.created)
        s.turns = data.get("turns") or []
        s.undone = data.get("undone") or []
        return s

    @classmethod
    def latest(cls, root: str) -> "Session | None":
        rows = listing(root)
        return cls.load(root, rows[0]["id"]) if rows else None

    # --------------------------------------------------------------- turns

    def add(self, prompt: str, outcome: str, edits: list, usage: dict,
            steps: int = 0) -> Turn:
        turn = Turn({
            "at": time.time(), "prompt": prompt, "outcome": outcome, "steps": steps,
            "edits": [{"path": e.path, "before": e.before, "after": e.after}
                      for e in edits],
            "usage": usage,
        })
        self.turns.append(turn)
        self.undone = []                 # a new turn ends the redo branch
        if not self.title:
            self.title = prompt.strip().splitlines()[0][:70]
        self.save()
        return turn

    def note(self, prompt: str, text: str) -> None:
        """A turn with no code change — a shell command, an answered question."""
        self.turns.append(Turn({"at": time.time(), "prompt": prompt,
                                "outcome": text, "steps": 0, "edits": [], "usage": {}}))
        self.save()

    # ------------------------------------------------------- undo and redo

    def undo(self) -> tuple:
        """Roll the last turn back on disk. Returns (turn, files restored)."""
        for index in range(len(self.turns) - 1, -1, -1):
            turn = self.turns[index]
            if not turn.get("edits"):
                continue
            restored = _restore(self.root, turn["edits"], "before")
            self.undone.append(self.turns.pop(index))
            self.save()
            return turn, restored
        return None, []

    def redo(self) -> tuple:
        if not self.undone:
            return None, []
        turn = self.undone.pop()
        restored = _restore(self.root, turn["edits"], "after")
        self.turns.append(turn)
        self.save()
        return turn, restored

    # --------------------------------------------------------------- money

    def spent(self) -> dict:
        total = {"decisions": 0, "requests": 0, "writer_calls": 0,
                 "cost": 0.0, "currency": "", "seconds": 0.0}
        for turn in self.turns:
            u = turn.get("usage") or {}
            for key in ("decisions", "requests", "writer_calls"):
                total[key] += int(u.get(key) or 0)
            total["cost"] += float(u.get("cost") or 0.0)
            total["seconds"] += float(u.get("seconds") or 0.0)
            total["currency"] = total["currency"] or (u.get("currency") or "")
        return total

    def transcript(self) -> str:
        out = ["# %s" % (self.title or "jevcode session"), ""]
        for turn in self.turns:
            out.append("## " + (turn.get("prompt") or "").strip())
            out.append("")
            out.append(turn.get("outcome") or "")
            for edit in turn.get("edits") or []:
                out.append("")
                out.append("- changed `%s`" % edit["path"])
            out.append("")
        return "\n".join(out)


def _restore(root: str, edits: list, field: str) -> list:
    done = []
    for edit in reversed(edits) if field == "before" else edits:
        full = os.path.join(root, edit["path"])
        body = edit.get(field) or ""
        if field == "before" and body == "":
            # The turn created this file; undoing means removing it again.
            if os.path.exists(full):
                os.unlink(full)
                done.append(edit["path"])
            continue
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body)
        done.append(edit["path"])
    return done


def listing(root: str, limit: int = 50) -> list:
    """Sessions of this project, newest first."""
    folder = store(root)
    rows = []
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        rows.append({"id": data.get("id") or name[:-5],
                     "title": data.get("title") or "(no title)",
                     "updated": data.get("updated") or 0,
                     "turns": len(data.get("turns") or [])})
    rows.sort(key=lambda r: -r["updated"])
    return rows[:limit]


def ago(when: float) -> str:
    gap = max(0, time.time() - when)
    for size, name in ((86400, "d"), (3600, "h"), (60, "m")):
        if gap >= size:
            return "%d%s ago" % (gap // size, name)
    return "just now"
