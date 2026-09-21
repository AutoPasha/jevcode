"""Every question the agent ever asks, and every threshold it compares against.

Deliberately one file. The questions *are* the program: change a word here and
the agent behaves differently, so they should be reviewable in one sitting
rather than scattered through the engine. The thresholds live here for the same
reason.

House rules, learned from the model's own documented rough edges:

* one question, one property — never fold two judgements into one sentence;
* say the exact condition, not the intent behind it, because the model reads
  literally;
* point at state by path in backticks (`open_file.body`) instead of pasting the
  data into the sentence;
* keep arithmetic, counting and ordering in Python, never in a question;
* a Choice is relative ("which of these"), a Noul is absolute ("is this true") —
  do not carry a threshold from one to the other.
"""

from __future__ import annotations

from .systemone import choice, noul, score

# ---------------------------------------------------------------- thresholds

DONE = 0.80            # task is finished and verified
HUMAN = 0.75           # stop and ask a person
ENOUGH_CONTEXT = 0.55  # enough of the code is open to write the change
DANGEROUS = 0.30       # a command that may destroy work — low bar on purpose
OFF_TASK = 0.70        # a command unrelated to the task
ACT_CONFIDENCE = 0.45  # below this the action choice is treated as a guess
FILE_HIT = 0.35        # a file shortlist entry worth opening
PATCH_OK = 0.50        # candidate patch is plausible enough to try

# ------------------------------------------------------------------ actions

ACTIONS = {
    "read": {
        "what": "Open a file, or another region of the file already open, to see code that has not been read yet",
        "not_for": "Reading a file whose relevant part is already in `open_file`",
    },
    "search": {
        "what": "Grep the repository for a literal string to find where something is defined or used",
        "not_for": "Looking again for a string that was already searched in `history`",
    },
    "edit": {
        "what": "Write the change: replace a region of a file that is already open",
        "not_for": "Editing a file whose contents have not been read yet",
    },
    "create": {
        "what": "Create a new file that does not exist yet",
        "not_for": "Changing a file that already exists",
    },
    "run": {
        "what": "Run one of the project's own commands, such as its tests or linter, to check the work",
        "not_for": "Running anything before there is a change to check",
    },
    "finish": {
        "what": "Stop: the task is carried out and a command has confirmed it",
        "not_for": "Stopping while the change is untested or unwritten",
    },
}


def _done(has_commands: bool, has_pages: bool) -> dict:
    """"Finished" means something different in a project that cannot be run.

    The original question demanded a green command, which is right for a
    repository with a test suite and impossible for one without: a landing page
    declares no pytest, no npm script and no Makefile, so the honest answer was
    always "false" and the agent could never stop. Where there is nothing to
    run, the evidence is the code itself plus `page_check`, which is a fact
    about the tree rather than an opinion about it.
    """
    if has_commands:
        return noul({
            "question": "Is `task` fully carried out in the code AND confirmed by a command in `history`?",
        }, {"true": "The change exists in the code and a run of the project's own command passed after it",
            "false": "The change is missing, partial, or was never checked by running anything"})
    if has_pages:
        return noul({
            "question": "Does the code now in the repository contain everything `task` asks for, with no problems left in `page_check`?",
        }, {"true": "Every file and every part the task names exists in the code, and `page_check.problems` is empty",
            "false": "Something the task asks for is missing or incomplete, or `page_check.problems` still lists something"})
    return noul({
        "question": "Does the code now in the repository contain everything `task` asks for?",
    }, {"true": "Every file and every part the task names has been written",
        "false": "Something the task asks for is still missing or incomplete"})


def step(files: dict, regions: dict, commands: dict, queries: dict,
         has_open_file: bool, has_edits: bool, has_pages: bool = False) -> dict:
    """One speculative fan-out: the decision and every argument it might need.

    Only some of these answers get used — whichever the chosen action calls for.
    Asking for all of them costs one request instead of four round trips, which
    is the whole reason the loop can afford to think before every move.
    """
    q = {
        "action": choice({
            "question": "What should the agent do next to carry out `task`?",
            "consider": "`history` is what has already been done and what it returned. "
                        "Do not repeat a step from `history` that produced nothing new.",
        }, {name: spec for name, spec in ACTIONS.items()
            if not (name in ("edit",) and not has_open_file)}),
        "done": _done(bool(commands), has_pages),
        "needs_human": noul({
            "question": "Does carrying out `task` require a decision only the repository's owner can make?",
        }, {"true": "It needs a product decision, a credential, or permission to delete or publish something",
            "false": "It is an ordinary code change that can be made from what is in the repository"}),
        "enough_context": noul({
            "question": "Is enough of the code open in `open_file` to write the change `task` asks for, without opening anything else?",
        }, {"true": "The code that has to change is visible in `open_file`",
            "false": "The relevant code has not been read yet"}),
    }
    if files:
        q["file"] = choice({
            "question": "Which file does `task` most likely need next?",
            "consider": "Pick where the change belongs or where the answer lives, not merely a file that mentions similar words.",
        }, files)
    if regions:
        q["region"] = choice({
            "question": "Which region of `open_file` does `task` require changing?",
        }, regions)
    if commands:
        q["command"] = choice({
            "question": "Which of the project's commands would show whether `task` is now done?",
        }, commands)
    if queries:
        q["query"] = choice({
            "question": "Which string, searched literally across the repository, would find the code `task` is about?",
        }, queries)
    for name in ACTIONS:
        q["worth_" + name] = noul({
            "question": "Right now, would `%s` produce something the agent does not already have in `history`?" % name,
            "action": ACTIONS[name]["what"],
        })
    if has_edits:
        q["more_places"] = noul({
            "question": "Does `task` still need a change somewhere other than the files listed in `edits_made`?",
        }, {"true": "Another file or another function has to change as well for the task to be carried out",
            "false": "Everything the task asks for is already in the edits listed"})
        q["regressed"] = noul({
            "question": "Do the edits listed in `history` change behaviour that `task` did not ask to change?",
        }, {"true": "Something unrelated to the task was altered or removed",
            "false": "Only what the task asked for was touched"})
    return q


def locate_file(shortlist: dict, task: str) -> dict:
    """Which file, out of up to 255 candidates, and is any of them right at all."""
    return {
        "file": choice({
            "question": "Which file holds the code that `task` is about?",
            "task": task,
        }, shortlist),
        "any": noul({
            "question": "Does any file in `candidates` hold the code that `task` is about?",
            "task": task,
        }, {"true": "At least one listed file contains it",
            "false": "None of them do; the code lives somewhere not listed"}),
    }


def locate_line(total_lines: int, task: str) -> dict:
    """Which line of an opened file the change belongs at.

    Lines are the option set: `L007` is an option, not a number the model has to
    count to. Anything past the first 255 lines is handled by the caller, which
    slices the file and asks again — the model never counts.
    """
    return {
        "line": choice({
            "question": "Which line of the file is the one `task` is about?",
            "task": task,
            "how": "Pick the single line that would have to change, or the line the answer is on.",
        }, {"L%03d" % i: None for i in range(1, total_lines + 1)}),
        "present": noul({
            "question": "Is the code `task` is about present in this file at all?",
            "task": task,
        }),
    }


def judge_patches(letters: list, task: str) -> dict:
    """Pick the best candidate patch, and decide whether any is good enough.

    Choice settles *which* one, Nouls settle *whether any*. They answer
    different questions and their numbers are not comparable, which is exactly
    why both are here.
    """
    q = {
        "best": choice({
            "question": "Which candidate correctly makes the change `task` asks for, in the place shown by `context`?",
            "how": "Read each candidate against the surrounding code. Ignore formatting and style.",
            "task": task,
        }, {letter: None for letter in letters}),
    }
    for letter in letters:
        q["works_" + letter] = noul({
            "question": "Would `candidates.%s` run correctly and do what `task` asks?" % letter,
            "task": task,
            "how": "Consider whether it fits the surrounding code, keeps the existing behaviour it should keep, and handles the edge cases the task implies.",
        })
        q["scope_" + letter] = noul({
            "question": "Does `candidates.%s` change something `task` did not ask to change?" % letter,
        }, {"true": "It removes, renames or rewrites code unrelated to the task",
            "false": "It touches only what the task is about"})
    return q


def guard_command(command: str, task: str) -> dict:
    """Three cheap questions in front of every shell command.

    A wrong `rm -rf` costs a working tree; three Nouls cost a fraction of a
    cent and about two hundred milliseconds, so the agent can afford to ask
    before every single command rather than pattern-matching on a deny list.
    """
    return {
        "destructive": noul({
            "question": "Would running `command` destroy work that cannot be recovered?",
            "command": command,
        }, {"true": "It deletes files, drops data, rewrites history, or force-pushes",
            "false": "It only reads, builds, tests, or writes inside the working tree"}),
        "off_task": noul({
            "question": "Is `command` unrelated to `task`?",
            "command": command,
            "task": task,
        }),
        "outside": noul({
            "question": "Does `command` touch anything outside this repository — the network, the home directory, or system paths?",
            "command": command,
        }),
    }


def read_result(text: str, task: str) -> dict:
    """What a command's output means, without a human reading it."""
    return {
        "verdict": score({
            "question": "What does `output` say about the state of `task`?",
            "output": text[-4000:],
            "task": task,
        }, [
            "The run failed for a reason unrelated to the task, such as a missing tool or a broken environment",
            "The run failed because the change is wrong or incomplete",
            "The run passed but does not exercise what the task changed",
            "The run passed and covers what the task changed",
        ]),
        "our_fault": noul({
            "question": "Was the failure in `output` caused by the change the agent just made?",
            "output": text[-4000:],
        }),
    }


def pick_new_file(options: dict, task: str) -> dict:
    """Where a file that does not exist yet should go, and whether to make one."""
    return {
        "path": choice({
            "question": "Which of these paths should a new file created for `task` have?",
            "task": task,
            "how": "Prefer a path the task names, then the directory where files of this kind already live.",
        }, options),
        "needed": noul({
            "question": "Does carrying out `task` require a file that does not exist yet?",
            "task": task,
        }, {"true": "The task asks for something new that has no place in the existing files",
            "false": "It can be done by changing files that already exist"}),
    }


MORE_PLACES = 0.60     # keep going: the change is not finished elsewhere
NEW_FILE = 0.55        # a new file is genuinely called for
