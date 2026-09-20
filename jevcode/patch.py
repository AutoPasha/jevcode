"""Writing the change: several candidates, then judgement.

A small model asked once is a coin flip. The same model asked eight times in
parallel almost always has a right answer somewhere in the pile — the hard part
is knowing which one it is. That is precisely the shape of question System One
answers well, and it answers it for a fraction of a cent, so the agent buys
variety from the cheap writer and spends the judgement on Jev.

Order matters here. Candidates are filtered by things that are facts before
anything is judged: did it change the file at all, does it still parse. Opinion
is only asked about candidates that survive, which is both cheaper and more
accurate than asking about all of them.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field

from . import act, questions

LETTERS = string.ascii_uppercase

SYSTEM = ("You are the writing half of a coding agent. Another model has already "
          "decided what to change and where. Write only the replacement code for "
          "the region you are given. No explanation, no diff markers, no "
          "surrounding code — a single fenced code block containing exactly the "
          "lines that replace the region, at the same indentation level.")

BRIEF = """Task: {task}

File: {path}
You are replacing lines {start}-{end} of this file. Here is the file around that
region, with line numbers for orientation only (do not include them in your reply):

{context}

The exact lines you are replacing:

{region}
{related}{failure}
Write the replacement for lines {start}-{end}. Keep everything the task does not
ask you to change. Reply with one fenced code block and nothing else."""

RELATED = """
Other code that has to keep working with your change:

{body}
"""

FAILURE = """
A previous attempt at this region was rejected by `{command}`:

{output}

Do not repeat that mistake.
"""


@dataclass
class Candidate:
    letter: str
    code: str
    text: str = ""
    rejected: str = ""
    probability: float = 0.0
    works: float = 0.0
    scope: float = 0.0
    seconds: float = 0.0

    @property
    def alive(self) -> bool:
        return not self.rejected


@dataclass
class PatchResult:
    candidates: list = field(default_factory=list)
    chosen: Candidate | None = None
    confidence: float = 0.0
    reason: str = ""

    @property
    def alive(self) -> list:
        return [c for c in self.candidates if c.alive]


def context_window(repo, rel: str, start: int, end: int, margin: int = 40) -> str:
    total = len(repo.read(rel).splitlines())
    return repo.numbered(rel, max(1, start - margin), min(total, end + margin))


def write(one, writer, repo, rel: str, start: int, end: int, task: str,
          n: int = 6, related: str = "", failure: tuple = ()) -> PatchResult:
    """n candidate replacements for one region, filtered in code, ranked by Jev.

    `related` is other code the change has to keep working with — usually the
    test that describes the change. `failure` is what the project's own command
    said about the previous attempt, so the writer is told what not to repeat.
    """
    body = repo.read(rel).splitlines()
    region_text = "\n".join(body[start - 1:end])
    prompt = BRIEF.format(
        task=task, path=rel, start=start, end=end,
        context=context_window(repo, rel, start, end),
        region=region_text or "(empty)",
        related=RELATED.format(body=related[:4000]) if related else "",
        failure=FAILURE.format(command=failure[0], output=failure[1][-1500:]) if failure else "")
    drafts = writer.drafts(prompt, n=n, system=SYSTEM)

    seen, cands = {}, []
    for draft in drafts:
        letter = LETTERS[len(cands)]
        cand = Candidate(letter, draft.code, draft.text, seconds=draft.seconds)
        if draft.error:
            cand.rejected = "writer failed: " + draft.error
        elif not draft.code.strip():
            cand.rejected = "empty"
        elif draft.code.strip() == region_text.strip():
            cand.rejected = "identical to the current code"
        else:
            merged = act.replace_region(repo.read(rel), start, end, draft.code)
            problem = act.syntax_error(rel, merged)
            if problem:
                cand.rejected = "does not parse: " + problem
            elif draft.code.strip() in seen:
                cands.append(cand)
                cand.rejected = "same as candidate " + seen[draft.code.strip()]
                continue
            else:
                seen[draft.code.strip()] = letter
        cands.append(cand)

    alive = [c for c in cands if c.alive]
    if not alive:
        return PatchResult(cands, None, 0.0, "every candidate was rejected before judging")
    if len(alive) == 1:
        return PatchResult(cands, alive[0], 0.0, "only one candidate survived the checks")

    state = {
        "task": task,
        "file": rel,
        "context": context_window(repo, rel, start, end, margin=25),
        "current_code": region_text,
        "candidates": {c.letter: c.code[:6000] for c in alive},
    }
    a = one.ask(state, questions.judge_patches([c.letter for c in alive], task))
    probs = a.probs("best")
    for c in alive:
        c.probability = probs.get(c.letter, 0.0)
        c.works = a.p("works_" + c.letter)
        c.scope = a.p("scope_" + c.letter)
    ranked = sorted(alive, key=lambda c: -c.probability)
    return PatchResult(cands, ranked[0], a.confidence("best"),
                       "picked out of %d candidates" % len(alive))


def next_best(result: PatchResult, exclude: set) -> Candidate | None:
    """The runner-up, for when the tests reject the winner.

    Backtracking costs nothing here: the other candidates are already written
    and already judged, so a failed test means trying the next one rather than
    another round trip to the writer.
    """
    rest = [c for c in result.alive if c.letter not in exclude]
    if not rest:
        return None
    return sorted(rest, key=lambda c: -c.probability)[0]
