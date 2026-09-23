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

WHOLE_SYSTEM = (
    "You are the writing half of a coding agent. Another model has decided that "
    "what you are given is a skeleton to be written, not code to be patched. "
    "Write it in full: every branch and every helper it needs, fully "
    "implemented. No explanation, no diff markers, no placeholders and no "
    "`pass` — a single fenced code block containing the finished code.")

WHOLE_BRIEF = """Task: {task}

Write {what} in full. What is there now is only a skeleton — the names and the
arguments are right, the bodies are empty, and your job is to implement them
without changing what anything else already calls.
{context}
What you are replacing:

{region}
{related}{failure}
Reply with one fenced code block holding {what}, with everything actually
implemented and nothing left as a placeholder. Nothing else."""

WHOLE_CONTEXT = """
The file it lives in, with line numbers for orientation only (do not include
them in your reply). You are replacing lines {start}-{end} of it:

{context}
"""

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


class Sifter:
    """Facts before opinion, one draft at a time.

    What does not change anything, does not parse, or is a copy of another
    draft is rejected here, for free, before Jev is asked about anything. It
    works per draft rather than per pile because the pile is no longer the unit
    of work: a draft that lands first gets sifted and put under the tests while
    the rest are still being written.
    """

    def __init__(self, rel: str, region_text: str, merge):
        self.rel = rel
        self.region_text = region_text
        self.merge = merge
        self.seen: dict = {}
        self.candidates: list = []

    def add(self, draft) -> "Candidate":
        letter = LETTERS[len(self.candidates) % len(LETTERS)]
        cand = Candidate(letter, draft.code, draft.text, seconds=draft.seconds)
        if draft.error:
            cand.rejected = "writer failed: " + draft.error
        elif draft.truncated:
            cand.rejected = "cut off at the token limit"
        elif not draft.code.strip():
            cand.rejected = "empty"
        elif draft.code.strip() == self.region_text.strip():
            cand.rejected = "identical to the current code"
        else:
            problem = act.syntax_error(self.rel, self.merge(draft.code))
            if problem:
                cand.rejected = "does not parse: " + problem
            elif draft.code.strip() in self.seen:
                cand.rejected = "same as candidate " + self.seen[draft.code.strip()]
            else:
                self.seen[draft.code.strip()] = letter
        self.candidates.append(cand)
        return cand


def _sift(rel: str, drafts: list, region_text: str, merge) -> list:
    """The whole pile at once, for the callers that have it all in hand."""
    sifter = Sifter(rel, region_text, merge)
    for draft in drafts:
        sifter.add(draft)
    return sifter.candidates


def context_window(repo, rel: str, start: int, end: int, margin: int = 40) -> str:
    total = len(repo.read(rel).splitlines())
    return repo.numbered(rel, max(1, start - margin), min(total, end + margin))


def budget(region_text: str, related: str, whole: bool) -> int:
    """How much room the writer gets, sized to what it has been asked for.

    A patch to one function fits in a fixed budget; a whole file written from a
    skeleton does not, and a reply cut off halfway through a class is thrown out
    as unparseable — which looks, from the trace, exactly like a model that
    cannot write the task. Implementations run about the size of the tests that
    describe them, so the tests set the ceiling.

    The floor is high because the writers worth using now think before they
    answer, and that thinking is spent out of the same allowance as the code.
    Nothing is paid for room that goes unused; a first live run at half these
    numbers spent longer asking twice than it would have spent asking once.
    """
    if not whole:
        return 4000
    chars = max(len(region_text), len(related) // 2, 1200)
    return int(min(12000, max(8000, chars / 2.5)))


def draft(one, writer, repo, rel: str, start: int, end: int, task: str,
          n: int = 6, related: str = "", failure: tuple = (),
          settle_at: int = 0, settle_grace: float = 2.5,
          whole: bool = False, label: str = "") -> PatchResult:
    """n candidate replacements for one region, filtered in code, not yet judged.

    `related` is other code the change has to keep working with — usually the
    test that describes the change. `failure` is what the project's own command
    said about the previous attempt, so the writer is told what not to repeat.
    `whole` says the region is a skeleton to be authored rather than code to be
    patched — the whole file, or one empty class inside it — which changes both
    the brief and how much room the writer is allowed.

    Nothing here asks Jev anything. Judgement is a separate call because the
    project's own tests usually answer the same question better and for free;
    see `judge` for when there is nothing to run.
    """
    brief = compose(repo, rel, start, end, task, related=related, failure=failure,
                    whole=whole, label=label)
    drafts = writer.drafts(brief.prompt, n=n, system=brief.system,
                           max_tokens=brief.room, enough=settle_at, grace=settle_grace)
    cands = _sift(rel, drafts, brief.region_text, brief.merge)
    alive = [c for c in cands if c.alive]
    if not alive and any(c.rejected.startswith("cut off") for c in cands):
        cands = roomier(writer, brief, rel, n, settle_grace)
        alive = [c for c in cands if c.alive]
    return settle(cands)


@dataclass
class Brief:
    """Everything one writing step needs, assembled once.

    Split out of `draft` because the pipeline needs the same brief without the
    waiting: it hands the prompt to the writer, sifts each draft as it lands and
    never has the pile in one place.
    """
    prompt: str
    system: str
    room: int
    region_text: str
    merge: object


def compose(repo, rel: str, start: int, end: int, task: str, related: str = "",
            failure: tuple = (), whole: bool = False, label: str = "") -> Brief:
    """The brief for one region: what to write, and how much room to write it in."""
    body = repo.read(rel).splitlines()
    region_text = "\n".join(body[start - 1:end])
    tail = (RELATED.format(body=related[:6000]) if related else "")
    tail += _house(rel)
    fail = (FAILURE.format(command=failure[0], output=failure[1][-1500:]) if failure else "")
    if whole:
        total = len(body)
        partial = start > 1 or end < total
        what = ("`%s` from %s" % (label, rel)) if (label and partial) else ("the whole of " + rel)
        context = (WHOLE_CONTEXT.format(start=start, end=end,
                                        context=context_window(repo, rel, start, end))
                   if partial else "")
        prompt = WHOLE_BRIEF.format(task=task, what=what, context=context,
                                    region=region_text or "(it is empty)",
                                    related=tail, failure=fail)
        system = WHOLE_SYSTEM
    else:
        prompt = BRIEF.format(
            task=task, path=rel, start=start, end=end,
            context=context_window(repo, rel, start, end),
            region=region_text or "(empty)", related=tail, failure=fail)
        system = SYSTEM
    merge = lambda code: act.replace_region(repo.read(rel), start, end, code)  # noqa: E731
    return Brief(prompt, system, budget(region_text, related, whole), region_text, merge)


def roomier(writer, brief: Brief, rel: str, n: int, settle_grace: float = 2.5) -> list:
    """One more attempt with twice the room, when every draft ran out of it.

    A pile that is entirely "cut off at the token limit" is a budget the code
    chose badly, not a task the writer cannot do, so it is worth the second
    round before giving up on the region.
    """
    drafts = writer.drafts(brief.prompt, n=max(2, n // 2), system=brief.system,
                           max_tokens=min(brief.room * 2, 16000),
                           enough=0, grace=settle_grace)
    return _sift(rel, drafts, brief.region_text, brief.merge)


def settle(cands: list) -> PatchResult:
    """What the pile amounts to once the free checks have had their say."""
    alive = [c for c in cands if c.alive]
    if not alive:
        return PatchResult(cands, None, 0.0, "every candidate was rejected before judging")
    if len(alive) == 1:
        return PatchResult(cands, alive[0], 0.0, "only one candidate survived the checks")
    return PatchResult(cands, None, 0.0, "%d candidates written" % len(alive))


def judge(one, repo, rel: str, start: int, end: int, task: str,
          result: PatchResult) -> PatchResult:
    """Ask Jev which of the surviving candidates is the change the task asked for.

    Only worth a request when the project cannot answer for itself. When there
    is a test command, running each candidate says the same thing as a fact,
    and a fact beats a probability every time.
    """
    alive = result.alive
    if len(alive) <= 1:
        result.chosen = alive[0] if alive else None
        return result
    body = repo.read(rel).splitlines()
    state = {
        "task": task,
        "file": rel,
        "context": context_window(repo, rel, start, end, margin=25),
        "current_code": "\n".join(body[start - 1:end]),
        "candidates": {c.letter: c.code[:6000] for c in alive},
    }
    a = one.ask(state, questions.judge_patches([c.letter for c in alive], task))
    probs = a.probs("best")
    for c in alive:
        c.probability = probs.get(c.letter, 0.0)
        c.works = a.p("works_" + c.letter)
        c.scope = a.p("scope_" + c.letter)
    ranked = sorted(alive, key=lambda c: -c.probability)
    result.chosen = ranked[0]
    result.confidence = a.confidence("best")
    result.reason = "picked out of %d candidates" % len(alive)
    return result


def write(one, writer, repo, rel: str, start: int, end: int, task: str, **kw) -> PatchResult:
    """Draft and judge in one call, for callers with nothing to run the code against."""
    result = draft(one, writer, repo, rel, start, end, task, **kw)
    if result.chosen is not None or not result.alive:
        return result
    return judge(one, repo, rel, start, end, task, result)


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


NEW_SYSTEM = ("You are the writing half of a coding agent. Another model has "
              "decided that a new file is needed and where it goes. Write the "
              "whole file and nothing else: one fenced code block, no "
              "explanation, no commentary.")

NEW_BRIEF = """Task: {task}

Create a new file: {path}

It has to fit a project that already contains these files:

{tree}
{related}{house}
Write the complete contents of {path}. Reply with one fenced code block and
nothing else."""

# What a page has to survive: being opened from disk, with no network. The
# first live run of the landing-page task came back with a hero pointing at
# unsplash and three feature icons pointing at via.placeholder.com — a domain
# that no longer resolves at all. On screen that is a grey rectangle and three
# broken-image glyphs, which is what "it cannot even do HTML" looks like.
HOUSE_MARKUP = """
House rules for markup and stylesheets in this project:
* the page must look finished with no network: no <img src> or CSS url()
  pointing at another domain, no placeholder image services, no hotlinked
  photographs;
* pictures and icons are made in the file itself — inline SVG, a CSS gradient,
  a styled shape, or a text glyph;
* every class used in the markup gets rules in the stylesheet, the footer and
  the form included;
* only files that exist in the tree, or ones this task also creates, may be
  linked with href or src.
"""


def _house(rel: str) -> str:
    return HOUSE_MARKUP if rel.lower().endswith(
        (".html", ".htm", ".css", ".scss")) else ""


def create(one, writer, repo, rel: str, task: str, n: int = 4, related: str = "",
           settle_at: int = 0, settle_grace: float = 2.5) -> PatchResult:
    """The same shape as an edit — several whole files, judged, one chosen.

    A new file has no region to replace and no surrounding code to match, which
    makes the writer *more* likely to wander, not less. So it gets the tree it
    is joining, and the candidates are judged against the task exactly as a
    patch would be.
    """
    tree = "\n".join(repo.files()[:120])
    prompt = NEW_BRIEF.format(
        task=task, path=rel, tree=tree,
        related=RELATED.format(body=related[:4000]) if related else "",
        house=_house(rel))
    room = budget("", related, whole=True)
    drafts = writer.drafts(prompt, n=n, system=NEW_SYSTEM, max_tokens=room,
                           enough=settle_at, grace=settle_grace)
    cands = _sift(rel, drafts, "", lambda code: code)
    alive = [c for c in cands if c.alive]
    if not alive and any(c.rejected.startswith("cut off") for c in cands):
        drafts = writer.drafts(prompt, n=max(2, n // 2), system=NEW_SYSTEM,
                               max_tokens=min(room * 2, 16000), enough=0,
                               grace=settle_grace)
        cands = _sift(rel, drafts, "", lambda code: code)
        alive = [c for c in cands if c.alive]
    if not alive:
        return PatchResult(cands, None, 0.0, "every candidate was rejected before judging")
    if len(alive) == 1:
        return PatchResult(cands, alive[0], 0.0, "only one candidate survived the checks")

    state = {"task": task, "file": rel, "context": "a new file in %s" % repo.root,
             "current_code": "(the file does not exist yet)",
             "candidates": {c.letter: c.code[:6000] for c in alive}}
    a = one.ask(state, questions.judge_patches([c.letter for c in alive], task))
    probs = a.probs("best")
    for c in alive:
        c.probability = probs.get(c.letter, 0.0)
        c.works = a.p("works_" + c.letter)
        c.scope = a.p("scope_" + c.letter)
    ranked = sorted(alive, key=lambda c: -c.probability)
    return PatchResult(cands, ranked[0], a.confidence("best"),
                       "picked out of %d candidates" % len(alive))
