"""Plain words for the page. One registry per kind of code, so no page text is built twice.

Every table here is checked by a test against the enum it words: a code with no entry is
a bug, never a `?` on the page.
"""

from .. import slices

# The stage of a slice, in the words the owner uses. REMEDIATE adds the review round.
# Rows the loop opens by itself each day; they are not in the roadmap tables.
DUTY_ROWS = {
    "DUTY-ROADMAP": "Daily roadmap update",
    "DUTY-RELEASE": "Evening engine release",
}

STAGE = {
    "FRAME": "Framing",
    "PLAN": "Planning",
    "DEVELOP": "Building",
    "SELF_CHECK": "Self check",
    "VERIFY": "Testing (QA)",
    "REVIEW": "In review",
    "REMEDIATE": "Fixing review findings",
    "GATE": "Ready to merge",
    "DOCUMENT": "Writing docs",
    "DONE": "Done",
    "PARKED": "Parked",
    "HELD": "Held",
}

RISK = {
    "R0": "low risk",
    "R1": "low risk",
    "R2": "medium risk",
    "R3": "needs you",
}

# What a worker did, by role icon (see live.ICONS). Used in the work history.
ACT = {
    "plan": "wrote the plan",
    "build": "built the change",
    "review": "reviewed the work",
    "test": "ran the tests",
    "docs": "wrote the docs",
    "duty": "ran a routine job",
}

RESULT = {"DONE": "done", "BLOCKED": "blocked", "FAILED": "failed"}
VERDICT = {
    "GO": "approved",
    "GO-WITH-FIXES": "approved with fixes",
    "NO-GO": "not approved",
}

# Who a slice waits on when no worker is running. The view picks one fixed word.
WAIT = ("slot", "apply", "owner", "stuck", "loop")

# Files and commands the page reads, with the plain name shown when one cannot be read.
# FILES are the dashboard's own readers; COMMANDS are the loop's snapshot commands as
# named in project.json (the loop records only their name, exit status and time).
FILES = {
    "merges": "Merge log",
    "closures": "Row closures",
    "questions": "Owner questions",
    "problems": "Problem log",
    "tick-state": "Tick state",
    "nodes": "Command results",
    "roadmap-titles": "Row names from the roadmap",
    "shipped-doc": "Recently shipped record",
    "roadmap-goal": "Goal rows from the roadmap",
}
COMMANDS = {
    "board": "Roadmap board",
    "roadmap": "Roadmap",
    "nodes": "Test nodes",
    "capacity": "Slot capacity",
    "big processes": "Big processes",
    "cli PRs": "CLI pull requests",
    "engine PRs": "Engine pull requests",
    "cli main CI": "CLI main checks",
    "engine main CI": "Engine main checks",
}
SLICE_FILES = "Slice records"


def stage(phase, rnd=0) -> str:
    words = STAGE[phase]
    if phase == "REMEDIATE" and rnd:
        return f"{words} (round {rnd})"
    return words


def command_label(name) -> str:
    """The shown name of a snapshot command: the registry first, else the config name."""
    if name in COMMANDS:
        return COMMANDS[name]
    text = " ".join(str(name).split())
    return (text[:1].upper() + text[1:])[:40] or "Unnamed command"


assert set(STAGE) == set(slices.PHASES)
