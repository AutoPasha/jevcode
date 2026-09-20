<p align="center">
  <img src="docs/assets/banner.svg" alt="jevcode" width="720">
</p>

<p align="center">
  <a href="https://autopasha.github.io/jevcode/">Project page</a> ·
  <a href="#install">Install</a> ·
  <a href="#in-the-terminal">Terminal</a> ·
  <a href="#what-it-does-on-real-work">Measurements</a> ·
  <a href="#against-other-agents">Comparison</a> ·
  <a href="#how-a-step-works">How it works</a>
</p>

A coding agent whose decisions are made by a model that cannot write a single
character of code.

[Jev](https://typesafe.ai) is a System One model. You hand it a state and typed
questions — is this true, which of these, where on this scale — and it answers
all of them at once with calibrated probabilities. It never writes text. Ask it
for a function and it has nothing to say.

That restriction buys something. A decision costs a fraction of a cent, comes
back in well under a second, and 256 of them fit in one request. So jevcode
asks constantly: which file does this task live in, which function, which of
these six drafts is correct, is this command about to delete something, is the
job actually done. A small fast model does the typing, and it is never asked
what to do.

```
$ jevcode "make Cart.total accept a discount argument, taken off before tax"

step 1 search  p=0.50 conf=0.54
        searched 'Cart' → 2 files
step 2 read    p=0.90 conf=0.98
        opened cart.py (21 lines)
step 3 edit    p=0.80 conf=0.86
        writing cart.py Cart.total (lines 14-15) — 6 candidates
        dropped 4: same as candidate A
        chose A (p=0.72, works=0.93, out-of-scope=0.10)
        python3 -m unittest discover -s tests -q passed with candidate A

done — task carried out and checked
73 decisions in 6 requests (5.2s, 41k tokens in) · 6 writer calls (12.6s) · 7.6s total
```

Every number on screen is a probability the model returned, not a summary of
what it was thinking. When the agent goes somewhere odd you can see which
question it answered badly, and fix that question.

## In the terminal

`jevcode` with no arguments opens a session in the current directory. It is a
prompt, not a dashboard: everything scrolls, everything can be copied out, and
the only thing that repaints is the status line.

<p align="center">
  <img src="docs/assets/session.svg" alt="a jevcode session in the terminal" width="720">
</p>

<sup>Recorded with `jevcode --demo`, which answers from a table so the interface
can be shown without a key. The same recording moving:
[docs/assets/demo.svg](docs/assets/demo.svg), and the raw cast beside it if you
would rather replay it yourself.</sup>

- `@path` puts a file in front of the agent, matched on any tail of its path.
- `!command` runs a shell command yourself without leaving.
- `/undo` and `/redo` move the files, not just the transcript — the text before
  and after every edit is kept in the session, so undo works in a directory that
  is not a git repository at all.
- Sessions survive the terminal closing: `jevcode -c` carries on, `/sessions`
  lists them.
- Before an edit is applied or a command is run you get the diff and a question,
  with "yes, and stop asking" as the second option. `--permission allow` for a
  script, `deny` to watch it think without letting it touch anything.
- `AGENTS.md` is read if the project has one — the same file the other terminal
  agents look for.

| command | |
| --- | --- |
| `/help` | the list |
| `/new` `/clear` | start over |
| `/sessions` `/resume` | list and switch |
| `/undo` `/redo` | move the files back and forward |
| `/diff` | everything this session changed |
| `/cost` | decisions, requests, seconds, money |
| `/models` `/model <name>` | who decides, who writes, swap the writer |
| `/details` | show or hide each decision as it is made |
| `/permission ask\|allow\|deny` | how much to ask |
| `/init` | write an `AGENTS.md` |
| `/export` `/editor` `/compact` `/exit` | |

Try the whole thing without a key:

```bash
jevcode --demo
```

That opens a throwaway sample project with one real bug in it and answers from a
canned table — no model is called and nothing is charged. It is a tour of the
interface, not of the quality; every number in this README comes from real runs.

## Why it is shaped this way

An ordinary coding agent spends its budget on deliberation. Each step means
running a large model over a growing transcript, so steps are slow, expensive,
and precious — which is why agents commit to the first plausible move and
discover the alternatives by walking into them.

Reverse the economics and the shape changes:

| | ordinary agent | jevcode |
| --- | --- | --- |
| a decision | seconds, cents, a full context replay | ~200 ms, ~$0.0001, no transcript |
| decisions per step | one | dozens, in one request |
| looking ahead | take the step and find out | score the whole tree first |
| picking among drafts | keep the first one | write six, judge six, keep the best |
| checking a command | a deny list of regexes | three questions about this command |
| context growth | every file read stays in the prompt forever | state is assembled per question |

The last row matters more than it looks. Nothing accumulates in a prompt here.
The state handed to Jev is built fresh for each request out of what the current
question needs, so a long session does not slowly poison itself with everything
it has ever read.

## What it does on real work

Measured on 2026-09-20, by the benchmarks in `bench/`, against this repository
and HumanEval.

**Judging beats guessing — where there is something to judge.** The writer
produces N drafts for each of 80 HumanEval tasks, the tests decide which ones
work, and Jev picks without ever seeing the tests:

| writer | one draft | Jev picks among N | ceiling |
| --- | --- | --- | --- |
| llama-3.2-3b (8 drafts) | 49% | **78%** | 85% |
| qwen3.5-9b (6 drafts) | 94% | 94% | 100% |

A three-billion-parameter model with a judge in front of it lands within seven
points of the best any of its drafts could do, at 1148 questions across 80
requests and five minutes of model time. The second row is the honest other
half: when the writer is already right 94% of the time on tasks this size, there
is nothing left for a judge to win, and it wins nothing. Candidates are
deduplicated before judging — a Choice splits one unit of probability across its
options, so three copies of the right answer split their own vote three ways and
lose to a single wrong one. That detail is worth ten points on the first row.

**Finding the place, with no index.** 15 questions about this repository, each
with a known answer. No embeddings, no vector store, nothing to keep fresh —
every run reads the tree from scratch: **13/15 correct**, 30 questions in 15
requests, 12 seconds total.

**Small repositories end to end.** `bench/tasks/` holds nine small projects,
each with a feature missing and a failing test that demands it. The agent gets
one sentence and the directory; the project's own tests decide. No partial
credit. The charts below are drawn straight from the results file by
`bench/chart.py`, so they cannot drift from the numbers.

<p align="center">
  <img src="docs/assets/solved.svg" alt="Solved, by task" width="560"><br>
  <img src="docs/assets/speed.svg" alt="Seconds per task" width="560">
</p>

All nine ran on 2026-09-20, one attempt each, writer MiniMax-M2.7:

| task | solved | decisions | requests | wall | cost |
| --- | --- | --- | --- | --- | --- |
| ttlcache — entries outliving their TTL | yes | 14 | 1 | 12.4s | 0.04 RUB |
| jsdedupe — dedupe in a JavaScript project | yes | 14 | 1 | 14.3s | 0.04 RUB |
| duration — report whole days | yes | 14 | 1 | 22.5s | 0.04 RUB |
| jsonflag — a `--json` flag on a CLI | yes | 14 | 1 | 22.6s | 0.04 RUB |
| cart — a discount argument | yes | 14 | 1 | 23.1s | 0.04 RUB |
| csvparse — doubled quotes inside a quoted field | yes | 14 | 1 | 51.0s | 0.04 RUB |
| slugify — a new module from scratch | yes | 81 | 7 | 57.2s | 0.28 RUB |
| retry — a max_delay cap in two functions | yes | 30 | 2 | 65.5s | 0.09 RUB |
| pagesize — thread an argument through two modules | yes | 82 | 6 | 83.1s | 0.25 RUB |

**Nine out of nine**, 0.10 RUB plus about $0.016 of writer per task. Six of them
take a single request to Jev: one fan-out of questions, one region written six
ways, one test run that picks the winner, done.

That single-request shape is what the earlier version of this table was missing.
On the same nine tasks this morning the agent solved five, and every failure
looked the same — over twenty-five requests and the note `ran out of
steps`. It was not getting hard tasks slowly wrong. It was finishing them and
not noticing: the loop asked the model whether the work was done while the
project's own test command had already answered. Three things changed that, and
each is a fact replacing an opinion — a green suite ends the run, a suite with
fewer failures counts as progress and is kept rather than undone, and the
drafts race each other through the real test command instead of being judged
one at a time.

What is left in the time column is the writer, not the decisions. csvparse takes
one request to Jev and fifty-one seconds, and nearly all of that is six drafts
being typed. Point `JEVCODE_WRITER_MODEL` at a faster model and the same task
comes out in fifteen seconds with nothing else changed — which is the argument
for keeping the decisions and the typing in separate models.

### Where the seconds go

`bench/profile.py` times a run by phase — wall clock, not a sum, since drafts
are written in parallel:

| task | total | System One | writer | your machine |
| --- | --- | --- | --- | --- |
| cart | 6.8s | 4.4s (65%) | 2.1s (31%) | 0.2s (4%) |
| retry | 22.4s | 10.0s (45%) | 11.6s (52%) | 0.8s (4%) |
| jsonflag | 43.7s | 11.6s (27%) | 31.7s (73%) | 0.4s (1%) |

Reading files, running tests and applying patches — the part people assume is
slow — is 1–4% of a run. A System One request costs about 0.8s whatever you ask
it, so on a short task the decisions dominate and on a long one the drafts do.
Both numbers move the same way: fewer round trips.

Keeping the connection open is worth measuring rather than assuming.
`bench/handshake.py` asks the same trivial question ten times each way:

| transport | median | first call |
| --- | --- | --- |
| fresh socket per call | 0.541s | 1.101s |
| kept-alive pool | 0.480s | 0.735s |

About 60ms a call, 11% — real on a run that makes fifty requests, and smaller
than it looked before it was measured. The handshake is cheap because the
gateway is near; on a distant endpoint the same pool saves a great deal more.
The honest conclusion is that the round trips themselves are the cost, which is
why the next thing worth building is deciding several steps in one request
rather than making the trip faster.

## Against other agents

The only comparison worth printing is one you can re-run, so the harness is in
the repository rather than the claims.

```bash
cp bench/contestants.json.example bench/contestants.json   # edit to what you have
python3 bench/compare.py --who all --repeat 3              # everyone, every task
python3 bench/chart.py                                     # redraw the pictures
```

Every contestant gets an untouched copy of the same directory, the same
sentence, the same time limit, and is judged by the project's own tests. No
prompt tuned per agent, no retries, no partial credit. `contestants.json` is a
plain list of command templates with `{dir}` and `{task}` in them — it is yours
to read and to argue with, which is the point.

What gets measured: how often the tests go green, wall-clock seconds, and what
the run cost where the provider reports it. Those are three different units and
they never share an axis.

Run on 2026-09-20, nine tasks, one attempt each, opencode 1.18.31 as the other
agent. Both write with the same model, so what is compared is the harness:

| agent | model | solved | median time | cost per task |
| --- | --- | --- | --- | --- |
| jevcode | Jev + MiniMax M2.7 | **9/9 (100%)** | 23.1s | 0.10 RUB + ~$0.016 |
| opencode | MiniMax M2.7 | 8/9 (89%) | 24.7s | not reported |
| opencode | qwen3-coder-30b | 1/9 (11%) | 18.6s | not reported |

Nine tasks is a small set, and a hundred per cent on it means "nothing here was
out of reach", not "this agent does not fail". Read the third row twice: it is
the same benchmark from the other end. qwen3-coder-30b is a capable coding
model, and in a conventional agent it solves one task in nine — it writes
TypeScript into a Python project, edits the test instead of the code, calls
`npm test` where there is a Makefile. What the decisions buy is not
intelligence, it is not getting lost.

Both agents were checked for the oldest way to pass a benchmark: every task
ships a `.protected` list naming its test files and its Makefile, and a run
that changed either of them does not count.

Cost is blank for the opencode rows because neither provider reports a price to
the agent — MiniMax bills a plan, and the gateway does not return usage. We are
not going to estimate someone else's bill and print it as a measurement.

## Install

```bash
pipx install git+https://github.com/AutoPasha/jevcode
# or: uvx --from git+https://github.com/AutoPasha/jevcode jevcode "..."
```

Two keys. Jev decides, and something cheap writes:

```bash
export TYPESAFE_API_KEY=...          # console.typesafe.ai/keys
export JEVCODE_WRITER_KEY=...        # any OpenAI-compatible endpoint
export JEVCODE_WRITER_URL=https://api.openai.com/v1/chat/completions
export JEVCODE_WRITER_MODEL=gpt-4o-mini
```

The writer should be small and fast. It is asked for six drafts at a time and
judged on all six, so throughput is worth more here than pedigree — a 9B model
at 91% one-shot ends up at 95% under the judge, and it is cheap enough to ask
six times.

## Use

```bash
jevcode                                  # a session here
jevcode ../other-project                 # ...or somewhere else
jevcode -c                               # carry on where you left off
jevcode --demo                           # tour it with no key at all

jevcode run "rename the --verbose flag to --loud everywhere"
jevcode run --dry-run "add retries to the HTTP client"   # the patch, applied to nothing
jevcode run --format json "..."                          # for scripts and CI
jevcode where "the retry backoff"                        # find code, two requests
jevcode plan "add a discount argument to Cart.total"     # three moves ahead, one look
jevcode init                                             # write an AGENTS.md
jevcode auth login                                       # save the two keys
jevcode session list                                     # what you have been doing
jevcode stats --days 7                                   # what it has cost you
```

Useful flags: `-C DIR` to work somewhere else, `-n 8` for more drafts per edit,
`--permission allow|deny`, `--no-commands` to forbid running anything at all,
`--max-steps`, `--trace run.jsonl` to put every probability on disk.

Settings layer, each beating the one before it: the defaults, `~/.config/jevcode/config.json`,
`.jevcode.json` in the project, the environment, then the flags you typed.
`jevcode config` prints the result and where each part came from.

## How a step works

One request to Jev carries every question the step might need — the decision
itself, and the arguments for each action it might choose:

```python
{
  "action":         choice({read, search, edit, create, run, finish}),
  "file":           choice(up to 255 files, described),
  "region":         choice(the functions and classes of the open file),
  "command":        choice(what the project itself declares: make, npm, pytest),
  "query":          choice(literal strings taken from the task),
  "done":           noul("carried out AND confirmed by a command?"),
  "needs_human":    noul("does this need a decision only the owner can make?"),
  "worth_read", "worth_edit", ...: noul("would this produce anything new?")
}
```

Most of those answers are thrown away — whichever the chosen action does not
need. They are speculative on purpose: a hundred extra questions cost about as
much as one, so the agent weighs every option before every move instead of
committing to the first one that looks plausible. `action` says what looks
right and `worth_*` says whether it would produce anything new; the code
multiplies them, so a popular but pointless move loses to a useful unlikely one.

Then the code does the work. The model never executes anything, and the code
never guesses.

### Four things that fall out of cheap decisions

**Candidates, not a candidate.** An edit asks the writer for six drafts at
once. Drafts that do not parse, that came back empty, or that are identical to
the current code are dropped in Python before anything is judged — facts first,
opinion second. Jev ranks what survives. If the project's tests then reject the
winner, the runner-up is already written and already judged, so backtracking
costs one test run and no model calls at all.

**A gate in front of every command.** Three questions — would this destroy
work, is it unrelated to the task, does it reach outside the repository — asked
about the actual command, every time. A deny list only catches the shapes
somebody thought of; this reads `find . -delete` the way a person does. A few
shapes are still refused outright, because no answer should be able to permit
them.

**Telling a broken toolchain from a broken patch.** A missing test runner fails
exactly like a wrong change. Jev scores the output on a four-level rubric, and
an environment fault keeps the edit instead of throwing it away.

**Growing the window when a region keeps failing.** Some changes cannot be made
in one place: a new argument has to appear both in the function that takes it
and in the one that passes it down, and patching either half alone leaves the
tests red. A region that has already failed is retried wider — its neighbours
first, then the whole file. On the benchmark that one rule turned a task the
agent had been grinding at for 26 steps into seven.

**Three moves ahead in one request.** `jevcode plan` runs a beam search over
future actions: each branch carries its own assumed history inside the state,
and each question addresses its branch by path, so one request answers "what
next" for the whole frontier. Width three, depth three, about a second. Paths
are scored by the geometric mean of their probabilities, so a longer plan is
not punished for being longer.

## What it is not good at

Jev is a System One model, and the [rough edges](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
are documented honestly by the people who trained it. It reads literally, it
does not count, it is not a calculator, and accuracy drops as you pile
irrelevant detail into the state. Everything numeric in this agent is done in
Python for that reason.

Beyond the model: jevcode edits one region at a time. Two functions in the same
file are fine — a region that fails its tests is retried wider, first with its
neighbours and then as the whole file — and since 0.2 the loop refuses to stop
while "does this still need a change somewhere else" comes back high, so a
two-file change is normal. Five files in one sentence is still optimistic.
Large repositories are handled by grep and a 255-file shortlist, which is
enough more often than it sounds, but it is not a substitute for knowing where
things are. Nothing here is streamed token by token, because the model that
decides has no tokens to stream.

It is version 0.2. Bring a repository under version control and read the diff.

## Running the benchmarks

```bash
python3 bench/locate.py                        # can it find the right file
python3 bench/bestofn.py --tasks 80 --n 6      # how much the judge adds
python3 bench/endtoend.py                      # every task, jevcode alone
python3 bench/compare.py --who all --repeat 3  # jevcode against other agents
python3 bench/profile.py --task retry          # where the seconds of a run go
python3 bench/chart.py                         # redraw the README's pictures
python3 -m unittest discover -s tests          # everything that needs no network
```

`bench/bestofn.py` downloads HumanEval on first run and executes model-written
code locally. Run it in a container if that bothers you — it should.

A full sweep takes long enough that something will interrupt it, so it is
worth running one pair at a time — `--only <task> --out bench/.runs/x.json` —
and stitching the pieces together afterwards with `python3 bench/merge.py`.
A run that dies at task seven then costs you task seven, not all nine.

## Using a gateway

Any endpoint that speaks the same protocol works in place of TypeSafe:

```bash
export JEVCODE_SYSTEMONE_URL=https://polza.ai/api/v1/systemone
export JEVCODE_SYSTEMONE_KEY=...
export JEVCODE_JEV_MODEL=typesafe/jev
```

## License

MIT.
