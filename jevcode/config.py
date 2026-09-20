"""Where settings come from, and in what order one beats another.

Four layers, each overriding the one before it: the defaults in this file, the
user's file, the project's file, the environment. Flags on the command line win
over all of them, because they are the layer the person is typing right now.

Nothing here talks to the network or the model. It is deliberately boring: a
config system that surprises you is worse than no config system.
"""

from __future__ import annotations

import json
import os

APP = "jevcode"

DEFAULTS = {
    "jev_model": "jev-latest",
    "systemone_url": "https://api.typesafe.ai/v1/systemone",
    "writer_model": "gpt-4o-mini",
    "writer_url": "https://api.openai.com/v1/chat/completions",
    "candidates": 6,
    "max_steps": 24,
    "settle_at": 4,             # judge once this many drafts are in
    "settle_grace": 2.5,        # ...giving the rest this long to catch up
    "permission": "ask",        # ask | allow | deny — for commands and edits
    "details": True,            # show each decision as it is made
    "theme": "auto",            # auto | mono
    "instructions": ["AGENTS.md", "CLAUDE.md", ".jevcode/instructions.md"],
}

ENV = {
    "jev_model": "JEVCODE_JEV_MODEL",
    "systemone_url": "JEVCODE_SYSTEMONE_URL",
    "writer_model": "JEVCODE_WRITER_MODEL",
    "writer_url": "JEVCODE_WRITER_URL",
}

PROJECT_FILES = (".jevcode.json", "jevcode.json")


def user_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP)


def data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP)


def user_file() -> str:
    return os.path.join(user_dir(), "config.json")


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    text = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("//"))
    try:
        loaded = json.loads(text or "{}")
    except ValueError as ex:
        raise RuntimeError("%s is not valid JSON: %s" % (path, ex)) from None
    return loaded if isinstance(loaded, dict) else {}


class Config(dict):
    """Settings, plus where each project-level file was found."""

    def __init__(self, root: str = "."):
        super().__init__(DEFAULTS)
        self.root = os.path.abspath(root)
        self.sources: list = []
        self.update(_load_layer(user_file(), self.sources))
        for name in PROJECT_FILES:
            path = os.path.join(self.root, name)
            if os.path.exists(path):
                self.update(_load_layer(path, self.sources))
                break
        for key, var in ENV.items():
            if os.environ.get(var):
                self[key] = os.environ[var]
                self.sources.append(var)

    def apply(self, **flags) -> "Config":
        """Command line flags: the last word, but only where one was given."""
        for key, value in flags.items():
            if value is not None:
                self[key] = value
        return self

    def instructions(self) -> str:
        """The project's own rules for agents, if it wrote any down.

        Same file every terminal agent reads — AGENTS.md — so a repository that
        already explains itself to one of them explains itself to this one too.
        """
        out = []
        for name in self.get("instructions") or []:
            path = os.path.join(self.root, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read(8000).strip()
            except OSError:
                continue
            if body:
                out.append("%s:\n%s" % (name, body))
        return "\n\n".join(out)

    def save_user(self, **changes) -> str:
        path = user_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = _read(path)
        current.update(changes)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        self.update(changes)
        return path


def _load_layer(path: str, sources: list) -> dict:
    data = _read(path)
    if data:
        sources.append(path)
    return data


INIT_TEMPLATE = """# {name}

<!-- Read by jevcode, and by every other terminal agent that looks for AGENTS.md. -->

## What this project is

{summary}

## Commands

{commands}

## House rules

- Keep changes small and in the style of the surrounding code.
- Do not add dependencies without saying why.
"""


def write_instructions(root: str, summary: str, commands: dict) -> str:
    """`jevcode init` — the AGENTS.md a repository should have had already."""
    path = os.path.join(root, "AGENTS.md")
    lines = ["- `%s` — %s" % (cmd, why) for cmd, why in (commands or {}).items()]
    body = INIT_TEMPLATE.format(
        name=os.path.basename(os.path.abspath(root)),
        summary=summary or "(describe it in a sentence)",
        commands="\n".join(lines) or "- (none found)")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


# ---------------------------------------------------------------- credentials

def auth_file() -> str:
    return os.path.join(data_dir(), "auth.json")


def credentials() -> dict:
    """Keys saved by `jevcode auth login`. The environment still wins over them."""
    return _read(auth_file())


def save_credential(name: str, value: str) -> str:
    path = auth_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    current = _read(path)
    if value:
        current[name] = value
    else:
        current.pop(name, None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(current, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def key_for(which: str) -> str:
    """`which` is "systemone" or "writer". Environment first, then the file."""
    env = {"systemone": ("JEVCODE_SYSTEMONE_KEY", "TYPESAFE_API_KEY"),
           "writer": ("JEVCODE_WRITER_KEY", "OPENAI_API_KEY")}[which]
    for name in env:
        if os.environ.get(name):
            return os.environ[name]
    return credentials().get(which, "")
