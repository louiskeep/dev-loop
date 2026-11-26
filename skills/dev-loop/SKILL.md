---
name: dev-loop
description: Run the dev loop for a project from one live orchestrator session, with slice runners as subagents. Use when the user says "dev loop", "start the dev loop", "check the dev loop state", or "stop the dev loop". Sets up the project, starts the heartbeat and the dashboard, reports status in plain language, and records owner answers.
---

# Dev loop

The dev loop runs from one live Claude Code session, the orchestrator. Slice runners are
its subagents. Each runner takes one slice from build to merge-ready. The orchestrator
reviews R2 work, gates, merges and talks to the owner. The full procedure is
`ORCHESTRATOR.md` in this folder.

State is kept in files under `~/.local/state/devloop/PROJECT/`. `devloop-state` records every
move, and the dashboard reads those files.

Replace PROJECT with the project name. If the user names none and one project exists,
use it.

## start

1. If `project.json` does not exist, ask the setup questions one at a time. Use
   `project.example.json` in this folder as the shape. Set `"mode": "live"`. Write the
   file to the state directory and show it to the user.
2. Check that `project.json` says `"mode": "live"`. A config with no `mode` runs as
   headless, and a cron tick could then start a second model session beside the
   orchestrator. If the mode is missing or `headless`, tell the user and stop until they
   set it to `live`. Check that `crontab -l` has no `devloop-tick` line for the project
   (`devloop-cron remove PROJECT` takes one out).
3. Install the commands if they are not on PATH: `devloop-install BIN_DIR` from the
   plugin folder.
4. Check the setup: `devloop-state PROJECT check`. Stop and report if any line says FAIL.
5. Copy `runner-header.md` from this folder to the project's config folder and fill in
   each `<...>` value. The README lists them.
6. Start the dashboard: `devloop-dash PROJECT --detach`. Give the user the Network URL it
   prints. It binds 127.0.0.1 unless `dash.host` in `project.json` (or `--host`) says
   otherwise. Set `"dash": {"host": "0.0.0.0"}` to open it to the local network.
7. Start the heartbeat: `/loop` with a prompt that names the goal, the roadmap, the
   project's process doc and `ORCHESTRATOR.md`. Each tick ends with a wake-up about 60
   minutes out. Hand-backs, background shells and Monitors wake the session in between.
8. Tell the user it started and give the dashboard URL.

## stop

1. Stop the `/loop` heartbeat in the orchestrator session.
2. Let running slice runners finish. Their hand-backs wait for the next start.
3. Stop the dashboard if the user asks: `devloop-dash PROJECT --stop`.

## status

For a quick look, give the dashboard URL from `devloop-state PROJECT check` (the line
starting `dash:`). For detail, read `STATUS.md`, the events journal, the slice files
(`devloop-state PROJECT slice list`), the known problems, the lessons and the questions
page. Answer in plain language:

- what shipped, and what is in flight by row;
- rows closed;
- PRs merged today, this week and in total (count from GitHub: a `gh pr merge` does not
  add to the dashboard counts);
- progress and what is left for each row;
- open questions and the defaults in use;
- stale or parked items;
- open problems, and lessons ready for the defect catalog.

## answer

When the user overrules a default or answers a held question, run
`devloop-state PROJECT answer KEY CHOICE`. Held slices and owner parks need the `approve`
choice on the question named `hold:KEY`. The command is refused inside a headless session.

## Headless mode (optional, off)

The plugin also has a cron-driven loop: `devloop-tick` starts a fresh model session per
tick, `devloop-dispatch` runs each step (build, test, review, fix) as its own detached run,
and `devloop-merge` merges only after its evidence checks (an applied builder run as the
head writer, the approved plan unchanged, the branch current with its base). `TICK.md` is
that tick's prompt. To use it, set `"mode": "headless"`, run
`env -i HOME="$HOME" PATH="$PATH" devloop-state PROJECT check`, `devloop-state PROJECT resume`
and `devloop-cron add PROJECT --bin-dir BIN_DIR`. Stop it with `devloop-state PROJECT pause`
and `devloop-cron remove PROJECT`. It is not the default: separate runs for each step cut
merges from 20 to 40 a day to 8 or 9.

## Files in this folder

- `ORCHESTRATOR.md`: the standard procedure for the live orchestrator.
- `runner-header.md`: the header for every slice-runner brief.
- `PLAYBOOK.md`: done state per phase, scenarios, testing, close-out.
- `TICK.md`: the prompt for a headless tick (headless mode only).
- `brief-template.md`: the frame for a headless worker brief (headless mode only).
- `project.example.json`: an example project config.
