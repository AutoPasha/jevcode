"""System One client: typed questions in, calibrated probabilities out.

Jev does not write text. You hand it a state and a map of typed questions, and
it answers every one of them independently, in a single round trip. That is the
whole interface, and the reason this agent is shaped the way it is: decisions
are cheap and parallel, so we make hundreds of them and keep the expensive
generative model for the one thing Jev cannot do.

Two transports are supported:

* TypeSafe directly — ``https://api.typesafe.ai/v1/systemone`` with
  ``TYPESAFE_API_KEY``.
* Any gateway that proxies the same endpoint — set ``JEVCODE_SYSTEMONE_URL``
  and ``JEVCODE_SYSTEMONE_KEY``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# Hard limits of one request, from the API reference: 255 options per Choice,
# 10 levels per Score. Questions per request are capped at 256.
MAX_OPTIONS = 255
MAX_LEVELS = 10
MAX_QUESTIONS = 256

RETRY_STATUS = (429, 500, 502, 503, 529)


def noul(instructions, criteria=None):
    """Yes/no. Returns the probability the answer is yes. No confidence field."""
    q = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, criteria):
    """One option out of a closed set. Returns the pick, all probabilities, confidence."""
    if len(criteria) > MAX_OPTIONS:
        raise ValueError("a Choice takes at most %d options, got %d"
                         % (MAX_OPTIONS, len(criteria)))
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions, levels):
    """Position on an ordered rubric you describe. Returns a weighted value."""
    levels = list(levels)
    if not 2 <= len(levels) <= MAX_LEVELS:
        raise ValueError("a Score takes 2..%d levels, got %d" % (MAX_LEVELS, len(levels)))
    return {"type": "score", "instructions": instructions, "criteria": levels}


@dataclass
class Usage:
    requests: int = 0
    questions: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    currency: str = ""
    seconds: float = 0.0

    def add(self, payload: dict, asked: int, seconds: float) -> None:
        u = payload.get("usage") or {}
        self.requests += 1
        self.questions += asked
        self.input_tokens += int(u.get("input_tokens") or 0)
        self.output_tokens += int(u.get("output_tokens") or 0)
        self.seconds += seconds
        for name, currency in (("cost_rub", "RUB"), ("cost_usd", "USD"), ("cost", "")):
            if u.get(name) is not None:
                self.cost += float(u[name])
                self.currency = self.currency or currency
                break


class Answers(dict):
    """Answers keyed the way the questions were, with typed readers."""

    def p(self, key: str) -> float:
        """Probability of yes for a Noul."""
        return float(self[key]["noul"])

    def pick(self, key: str) -> str:
        return self[key]["choice"]

    def probs(self, key: str) -> dict:
        return self[key]["probabilities"]

    def confidence(self, key: str) -> float:
        return float(self[key].get("confidence", 0.0))

    def ranked(self, key: str):
        """Options of a Choice, most probable first."""
        return sorted(self.probs(key).items(), key=lambda kv: -kv[1])

    def value(self, key: str) -> float:
        return float(self[key]["score"])


class SystemOne:
    def __init__(self, url=None, key=None, model=None, timeout=120, retries=4,
                 usage: Usage | None = None):
        self.url = url or os.environ.get("JEVCODE_SYSTEMONE_URL") or TYPESAFE_URL
        self.key = (key or os.environ.get("JEVCODE_SYSTEMONE_KEY")
                    or os.environ.get("TYPESAFE_API_KEY"))
        self.model = model or os.environ.get("JEVCODE_JEV_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries
        self.usage = usage if usage is not None else Usage()
        self.last_raw: dict = {}

    def ask(self, state, questions: dict) -> Answers:
        """One round trip. Every question sees the same state and is judged alone."""
        if not questions:
            return Answers()
        if len(questions) > MAX_QUESTIONS:
            out = Answers()
            keys = list(questions)
            for i in range(0, len(keys), MAX_QUESTIONS):
                out.update(self.ask(state, {k: questions[k] for k in keys[i:i + MAX_QUESTIONS]}))
            return out
        if not self.key:
            raise RuntimeError(
                "no System One key: set TYPESAFE_API_KEY, or JEVCODE_SYSTEMONE_KEY "
                "together with JEVCODE_SYSTEMONE_URL to go through a gateway")
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        delay = 0.7
        for attempt in range(self.retries + 1):
            started = time.time()
            req = urllib.request.Request(self.url, data=body, headers={
                "Authorization": "Bearer " + self.key,
                "Content-Type": "application/json",
                "User-Agent": "jevcode",
            })
            try:
                raw = urllib.request.urlopen(req, timeout=self.timeout).read()
            except urllib.error.HTTPError as ex:
                detail = ex.read().decode("utf-8", "replace")[:500]
                if ex.code in RETRY_STATUS and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError("System One returned %d: %s" % (ex.code, detail)) from None
            except urllib.error.URLError as ex:
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError("System One unreachable: %s" % ex) from None
            out = json.loads(raw)
            self.last_raw = out
            self.usage.add(out, len(questions), time.time() - started)
            return Answers(out["answers"])
        raise RuntimeError("System One: out of retries")
