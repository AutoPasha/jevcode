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
from dataclasses import dataclass, field

from . import net

DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)


def _extra_body() -> dict:
    """Provider knobs the brief cannot express, passed through as JSON.

    Every provider has a few of its own, and the one that matters here is the
    thinking switch. A model that reasons before it answers spends the same
    allowance the code has to come out of: asked for a whole class with room
    for 8000 tokens, MiniMax-M2.7 spent all 8000 thinking and returned nothing
    usable, four times out of four, in 110 seconds. Turning that off is one
    field in the body, so it is a setting rather than a patch:

        JEVCODE_WRITER_EXTRA='{"thinking": {"type": "disabled"}}'
    """
    raw = os.environ.get("JEVCODE_WRITER_EXTRA") or ""
    if not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _price(name: str) -> float:
    try:
        return float(os.environ.get(name) or 0.0)
    except ValueError:
        return 0.0


@dataclass
class WriterUsage:
    """What the writer spent.

    Gateways tend to put the price of a call in the usage block, and when one
    does we simply believe it. A provider's own API usually does not: it
    reports tokens and expects you to know its price list. For that case the
    rate per million tokens comes from the environment, so the number in a
    benchmark table is one anybody can recompute from a published price."""
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
                return
        rate_in, rate_out = _price("JEVCODE_WRITER_PRICE_IN"), _price("JEVCODE_WRITER_PRICE_OUT")
        if rate_in or rate_out:
            self.cost += (int(u.get("prompt_tokens") or 0) * rate_in
                          + int(u.get("completion_tokens") or 0) * rate_out) / 1e6
            self.currency = self.currency or (os.environ.get("JEVCODE_WRITER_CURRENCY") or "USD")


@dataclass
class Draft:
    """One candidate produced by the writer."""
    index: int
    text: str
    code: str
    seconds: float = 0.0
    error: str = ""
    truncated: bool = False


class Writer:
    def __init__(self, url=None, key=None, model=None, timeout=0, temperature=0.7,
                 usage: WriterUsage | None = None, extra: dict | None = None):
        self.url = url or os.environ.get("JEVCODE_WRITER_URL") or DEFAULT_URL
        self.key = (key or os.environ.get("JEVCODE_WRITER_KEY")
                    or os.environ.get("OPENAI_API_KEY"))
        self.model = model or os.environ.get("JEVCODE_WRITER_MODEL") or DEFAULT_MODEL
        # How long one draft may take. A writer that thinks before it answers
        # needs minutes, not the transport's default, and which writer is in
        # use is a setting rather than a code change.
        self.timeout = timeout or int(os.environ.get("JEVCODE_WRITER_TIMEOUT") or 300)
        self.temperature = temperature
        self.usage = usage if usage is not None else WriterUsage()
        self.extra = extra if extra is not None else _extra_body()

    def _once(self, prompt: str, system: str, temperature: float, max_tokens: int) -> tuple:
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        payload.update(self.extra)
        out, spent = net.post_json(self.url, payload, {
            "Authorization": "Bearer " + (self.key or ""),
            "User-Agent": "jevcode",
        }, self.timeout)
        self.usage.add(out, spent)
        choice = out["choices"][0]
        # A reply cut off at the token limit looks exactly like a syntax error
        # further down the line, and gets thrown away for the wrong reason. The
        # provider already says which it was, so believe it.
        stopped = (choice.get("finish_reason") or choice.get("stop_reason") or "")
        return choice["message"]["content"], spent, stopped == "length"

    def drafts(self, prompt: str, n: int = 4, system: str = "", max_tokens: int = 1600,
               spread: float = 0.25, enough: int = 0, grace: float = 2.5) -> list:
        """n candidates in parallel. Temperature fans out so they differ.

        The slowest of eight parallel calls sets the pace of the whole step, and
        it is usually an outlier rather than a better answer. So once `enough`
        of them are back the rest get `grace` seconds and are then abandoned:
        the judgement happens on the drafts that arrived, and a straggler that
        lands later simply does not compete. Set `enough` to 0 to wait for all.
        """
        if not self.key:
            raise RuntimeError("no writer key: set JEVCODE_WRITER_KEY (or OPENAI_API_KEY)")

        def one(i: int) -> Draft:
            temp = self.temperature + spread * (i / max(n - 1, 1))
            try:
                text, spent, cut = self._once(prompt, system, min(temp, 1.3), max_tokens)
            except net.HTTPError as ex:
                return Draft(i, "", "", 0.0, "HTTP %d: %s" % (ex.status, ex.body[:200]))
            except Exception as ex:                      # noqa: BLE001 - reported, not raised
                return Draft(i, "", "", 0.0, str(ex)[:200])
            return Draft(i, text, extract_code(text), round(spent, 2), truncated=cut)

        pool = cf.ThreadPoolExecutor(max(n, 1))
        futures = [pool.submit(one, i) for i in range(n)]
        try:
            if not enough or enough >= n:
                out = [f.result() for f in futures]
            else:
                out = _settle(futures, enough, grace)
        finally:
            pool.shutdown(wait=False)
        return sorted([d for d in out if d is not None], key=lambda d: d.index)


THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)
THINK_OPEN = re.compile(r"<(think|thinking|reasoning)>.*$", re.S | re.I)


def extract_code(text: str) -> str:
    """Take the fenced block if there is one, otherwise trust the whole reply.

    Thinking is cut out first. Models that reason in the open put it in the
    same field as the answer, and a reply that is all thinking with a code
    fence somewhere in the middle of it used to be read as code — including
    the model talking itself out of the version it then wrote.
    """
    text = THINK.sub("", text or "")
    blocks = FENCE.findall(text)
    if blocks:
        return max(blocks, key=len).strip("\n")
    return THINK_OPEN.sub("", text).strip()


def _settle(futures: list, enough: int, grace: float) -> list:
    """Wait for `enough` drafts, then give the rest `grace` seconds and move on."""
    done = []
    pending = list(futures)
    while pending and len(done) < enough:
        finished, pending_set = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
        for f in finished:
            done.append(f.result())
        pending = list(pending_set)
    if pending:
        finished, _ = cf.wait(pending, timeout=grace)
        for f in finished:
            done.append(f.result())
    return done
