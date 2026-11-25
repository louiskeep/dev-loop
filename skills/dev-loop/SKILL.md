---
name: dev-loop
description: Run the dev loop headless from cron. Use when the user says "dev loop", "start the dev loop cron", "check the dev loop state", or "stop the dev loop". Starts and stops the scheduled tick, reports status in plain language, and records owner answers.
---

# Dev loop

The dev loop is a scheduled tick that runs the same phases, reviews and merge rules as a
live session. State lives in files under `~/.local/state/devloop/PROJECT/`. The tick
changes state only through `devloop-state`, `devloop-dispatch` and `devloop-merge`.

Replace PROJECT with the project name. If the user names none and one project exists,
use it.

## start

1. If `project.json` does not exist, ask the setup questions one at a time. Use
   `project.example.json` in this folder as the shape: name, intent, roadmap, repos, rules
   directory, capacity, models, review rosters, reviewers and their commands, test
   commands, merge rules, notify command, board command, node directory, duties. Write
   the file to the state directory and show it to the user.
2. Install the commands if they are not on PATH: `devloop-install BIN_DIR` from the
   plugin folder.
3. Check the setup from a cron-like environment:
   `env -i HOME="$HOME" PATH="$PATH" devloop-state PROJECT check`. It validates the config
   (including a board phase for every phase), the tools it names, the roadmap path, the
   rules directory, each snapshot command, the notify settings, `gh auth status` and
   ssh to each node host. Stop and report if any line says FAIL.
4. Clear the pause: `devloop-state PROJECT resume`.
5. Run one tick in the same environment: `env -i HOME="$HOME" PATH="$PATH" devloop-tick PROJECT`.
6. Add the schedule: `devloop-cron add PROJECT --bin-dir BIN_DIR`. It keeps every other
   cron line and checks the result.
7. Tell the user it started and when the next tick runs.

## stop

1. `devloop-state PROJECT pause`.
2. `devloop-cron remove PROJECT`.
3. Tell the user that running workers finish and their results wait for the next start.

## status

Read `STATUS.md`, the events journal, the slice files (`devloop-state PROJECT slice
list`), the known problems, the lessons, the questions page, and the board command's
`show` when one is configured. Answer in plain language:

- what shipped, and what is in flight by row;
- rows closed;
- PRs merged today, this week and in total;
- progress and what is left for each row;
- open questions and the defaults in use;
- stale or parked items;
- open problems, and lessons ready for the defect catalog;
- the last tick time and the next one.

## answer

When the user overrules a default or answers a held question, run
`devloop-state PROJECT answer KEY CHOICE`. Held slices and owner parks need the `approve`
choice on the question named `hold:KEY`. The command is refused inside a headless session,
which is the point.

## Files in this folder

- `TICK.md`: the prompt each tick reads.
- `PLAYBOOK.md`: done state per phase, scenarios, brief parts, testing, close-out.
- `brief-template.md`: the frame every worker brief is built from.
- `project.example.json`: an example project config.
