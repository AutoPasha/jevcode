"""The writer: the only component allowed to produce text.

Jev decides everything — which file, which lines, which action, whether the
result is good. It cannot type a single character of code, so a small,
cheap, fast chat model does that part and nothing else. The writer is never
asked to plan, choose or judge; it gets a closed brief assembled in code from
Jev's answers and returns a block of code.

Because a small model is unreliable one shot at a time, we always ask for
several candidates in parallel and let Jev pick. That trade is the point of the
whole design: generation is the expensive, slow, unreliable half, so we buy
variety there and spend the judgement on a model that costs a fraction of a
cent per call.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)


@dataclass
class WriterUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    currency: str = ""
    seconds: float = 0.0

    def add(self, payload: dict, seconds: float) -> None:
        u = payload.get("usage") or {}
        self.calls += 1
        self.input_tokens += int(u.get("prompt_tokens") or 0)
        self.output_tokens += int(u.get("completion_tokens") or 0)
        self.seconds += seconds
        for name, currency in (("cost_rub", "RUB"), ("cost_usd", "USD"), ("cost", "")):
            if u.get(name) is not None:
                self.cost += float(u[name])
                self.currency = self.currency or currency
                break


@dataclass
class Draft:
    """One candidate produced by the writer."""
    index: int
    text: str
    code: str
    seconds: float = 0.0
    error: str = ""


class Writer:
    def __init__(self, url=None, key=None, model=None, timeout=180, temperature=0.7,
                 usage: WriterUsage | None = None):
        self.url = url or os.environ.get("JEVCODE_WRITER_URL") or DEFAULT_URL
        self.key = (key or os.environ.get("JEVCODE_WRITER_KEY")
                    or os.environ.get("OPENAI_API_KEY"))
        self.model = model or os.environ.get("JEVCODE_WRITER_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.temperature = temperature
        self.usage = usage if usage is not None else WriterUsage()

    def _once(self, prompt: str, system: str, temperature: float, max_tokens: int) -> tuple:
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={
            "Authorization": "Bearer " + (self.key or ""),
            "Content-Type": "application/json",
            "User-Agent": "jevcode",
        })
        started = time.time()
        raw = urllib.request.urlopen(req, timeout=self.timeout).read()
        out = json.loads(raw)
        spent = time.time() - started
        self.usage.add(out, spent)
        return out["choices"][0]["message"]["content"], spent

    def drafts(self, prompt: str, n: int = 4, system: str = "", max_tokens: int = 1600,
               spread: float = 0.25) -> list:
        """n candidates in parallel. Temperature fans out so they differ."""
        if not self.key:
            raise RuntimeError("no writer key: set JEVCODE_WRITER_KEY (or OPENAI_API_KEY)")

        def one(i: int) -> Draft:
            temp = self.temperature + spread * (i / max(n - 1, 1))
            try:
                text, spent = self._once(prompt, system, min(temp, 1.3), max_tokens)
            except urllib.error.HTTPError as ex:
                return Draft(i, "", "", 0.0, "HTTP %d: %s"
                             % (ex.code, ex.read().decode("utf-8", "replace")[:200]))
            except Exception as ex:                      # noqa: BLE001 - reported, not raised
                return Draft(i, "", "", 0.0, str(ex)[:200])
            return Draft(i, text, extract_code(text), round(spent, 2))

        with cf.ThreadPoolExecutor(max(n, 1)) as pool:
            out = list(pool.map(one, range(n)))
        return sorted(out, key=lambda d: d.index)


def extract_code(text: str) -> str:
    """Take the fenced block if there is one, otherwise trust the whole reply."""
    blocks = FENCE.findall(text or "")
    if blocks:
        return max(blocks, key=len).strip("\n")
    return (text or "").strip()
