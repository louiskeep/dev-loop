---
Status: plan
updated: 2026-11-25
---

# Remote Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Rules consulted: README, 00-universal, risk-and-exceptions, development-loop,
autonomous-operation, reuse-first, proportionality, architecture, cross-cutting-concerns,
security, config-secrets-and-supply-chain, observability-and-resilience,
destructive-operations, api-and-compatibility, testing, workspace-and-vcs,
profiles/local-operations.

Risk: **R2** (concurrency across hosts, a new trust boundary over ssh, a worktree
reservation change, remote process kills). Plan review: Senior reviewer (at most 2 rounds),
then Codex to green. Code review: Senior reviewer, then Codex, on the exact head. Repo
`/home/cam/dev-loop-plugin`, branch `feat/headless-loop`, planned from head `b195b37` plus
the OOM fix in progress in the working tree (MCP strip, `_check_memory` reservations,
`max_dispatch_per_tick`). Rebase this plan's tasks on that fix once it lands.

**Goal (owner, 2026-11-25):** let the Claude worker roles (Developer, Architect, Senior
reviewer, Tech writer) run on other LAN hosts, so a burst of workers no longer runs the
devbox (12 GB, 4 cores) out of memory. The devbox stays the fallback.

**Why:** a burst of 16 local `claude -p` workers at about 585 MB each caused an OOM. The OOM
fix caps the damage; this plan moves the load. Hosts: the Dell WSL2 node (43 GB, about 25 GB
free, CPU load 15 to 30 on 26 threads from tests) and the Mac mini (16 GB, often idle), from
`/home/cam/obsidian-vault/10_Projects/decoy/NODE-DIRECTORY.md`.

## Guarantee and non-goals

Guarantees:
1. State, the run ledger, request hashes, apply rules and evidence stay on the devbox and do
   not change shape except new frozen request fields (`host`, `remote`).
2. No remote writer runs unsupervised for longer than its deadline plus 10 minutes, and its
   worktree reservation and slot stay held until the remote process is confirmed dead.
3. One writer per worktree and one writer per slice branch, across all hosts.
4. A remote worker cannot push to GitHub, open or merge a PR: worker hosts hold no GitHub
   credentials for the run, and commits travel to the devbox over ssh only.
5. Every commit a remote worker makes reaches the devbox before its run is applied, or the
   run fails and the remote worktree is kept for recovery. Remote work is never deleted.

Non-goals: Codex kinds and test runs on worker hosts (tests already use `vm-run`); a pull
model where hosts poll for work; hostile-host defences (accident-only, single owner, LAN);
GPU or CPU-aware scheduling; Windows hosts; automatic host setup (setup is the owner's
documented procedure, checked by `devloop-state check`).

## Design

- **The wrapper stays on the devbox.** `devloop-wrap` runs locally for every run. For a
  remote run it starts the payload with `ssh -o BatchMode=yes` through a small launcher
  shipped to the host, `devloop-agent` (one stdlib Python file, Python 3.9 or later, so it
  runs on WSL and on macOS without `setsid` or `/proc`).
- **Three layers stop a stranded remote writer.** (a) Dead-man's switch: the wrapper keeps
  the ssh stdin pipe open and never writes it; the agent reads stdin in a thread and kills
  the payload's process group on EOF. (b) Kill on reconnect: when the local ssh process
  dies, the wrapper reconnects (5 tries over about 5 minutes) and kills the group named in
  the remote `pid.json` after checking its start time. (c) Remote self-deadline: the agent
  kills its payload at `deadline_s + 600` even if the devbox never comes back.
- **Confirmed dead or held.** A remote run whose host does not answer gets `exit` with
  reason `unconfirmed`. Like an unconfirmed test today, it keeps its slot and its worktree
  reservation. `collect` retries the check each tick: the host answers and the pid with its
  start time is gone, or the remote boot id changed, or the remote `exit` file exists. Then
  it writes `released`. The owner can also `devloop-state release RUN`.
- **Commits travel over ssh, never through GitHub.** Each host keeps a bare mirror per repo
  at `<workdir>/<project>/<repo>.git` and one worktree per slice at
  `<workdir>/<project>/wt/<key>`. Before the payload, the wrapper pushes the slice head
  from the devbox worktree to the mirror with a fast-forward-only push to
  `refs/heads/<branch>`. After the payload it fetches the branch back with
  `git fetch ssh://<host>/<mirror> <branch>` and fast-forwards the devbox worktree. The
  existing `resolve_head`, self-check, pinned checkouts and PR steps then run on the devbox
  unchanged. Pattern taken from `vm-run`, which pushes refs to the Dell's bare mirrors.
- **Results come back as files.** The payload writes `result.md` and `final.md` into the
  remote out dir (`DEVLOOP_RUN_DIR` points there). After it ends, the wrapper copies the
  remote out dir into the local out dir (`log` cut to its last 5 MB), then runs today's
  `finish`. `parse_result`, the ledger and `apply` read only the local copy.
- **Briefs and rules.** The brief goes over ssh stdin to the agent, which writes it to
  `brief.md` on the host and gives it to the payload as stdin. Rulebooks, the skill dir,
  the playbook and process pages and the node directory are copied with `rsync -a
  --delete --chmod=a-w` to `<workdir>/ref/` before each remote run. The brief's paths are
  rewritten through the host's `path_map` when it is built, so the brief names files that
  exist on that host.
- **Admission picks the host.** Under the existing admission lock, `select_host` returns
  the host for one run: the kind is in the host's `roles`, the host's `max[kind]` is free,
  the host answered a probe in the last 60 s, and its free memory less recent reservations
  less this kind's estimate stays above `memory_mb_free_min`. Order: the host that ran the
  slice's last writer run (affinity, so a slice's commits stay on one host), then remote
  hosts by most free memory, then `local`. The loop-wide `capacity` caps still apply.
- **Reservation keys.** `req["root"]` becomes `<host>:<canonical path>` (local runs use
  `local:`; a stored root with no prefix reads as `local:`). A second key, `<repo>:<branch>`,
  is reserved for writers, so two hosts cannot write the same branch.
- **Hooks.** Two layers. Structural: a worker host has no route to merge. The payload env
  sets `GH_CONFIG_DIR` to an empty dir under the workdir, sets `GH_TOKEN` and
  `GITHUB_TOKEN` to empty and `GIT_TERMINAL_PROMPT=0`, and the remote worktree's only remote
  is the host's bare mirror. Behavioural: the plugin is installed on each host (owner
  step), the plugin config's `rules_dirs` includes `<workdir>/ref/rules`, and the agent
  passes `DEVLOOP_HEADLESS=1` and `DEVLOOP_BASE_BRANCHES` explicitly, because ssh does not
  forward the environment. `devloop-state check` probes both hooks on the host (Task 6). A
  host whose probe fails is refused at admission.
- **Clocks.** All deadlines, idle limits and ages use the devbox clock. Remote start times
  and boot ids are compared only with values read from the same host. WSL clock skew does
  not affect any decision.

## Design notes

- Choke points: `remote.hosts.select_host` is the only place a run gets a host;
  `runs.reservation_keys(req)` is the only builder of reservation keys, used by admission
  and `writer_runs`; `remote.link.ssh_argv(host_cfg, *cmd)` is the only builder of ssh
  argv (BatchMode, ConnectTimeout 8, ServerAliveInterval 15, ServerAliveCountMax 4,
  ControlMaster with a ControlPath under the state dir). Guard tests walk `devloop/` for any
  other `"ssh"` argv literal and for `req["root"]` writes outside `reservation_keys`.
- Established pattern: Ansible async (remote job writes a status file, the controller polls
  and reads it) plus the stdin-EOF watchdog. Sources under Established solutions.
- One place per fact: host config in `project.json` `hosts`, validated in `project.py`;
  host probe cache in `hosts-cache.json`; the per-kind memory estimate from the OOM fix's
  `projmod.memory_mb`.
- House style: typed context (`HostCfg` named tuple) over long parameter lists, no flag
  arguments, comments that explain why.

## Placement

The repo has no CODEMAP. `devloop/` sits at 23 modules plus `dash/`, near the fan-out cap
of about 25, so the new modules go in a named sub-package.

| New file | Directory | Why there | Sits beside |
|---|---|---|---|
| `devloop/remote/__init__.py`, `hosts.py`, `link.py`, `sync.py` | `devloop/remote/` | fan-out cap; one concern each (config and choice, ssh and liveness, git and file sync) | `devloop/runs.py` |
| `devloop/remote/agent.py` | `devloop/remote/` | shipped to hosts as `devloop-agent`; stdlib only, no package imports | `devloop/wrap.py` |
| `tests/devloop/test_remote_*.py`, `fakebin/ssh` | `tests/devloop/` | existing test home and fake bin pattern | `test_wrap.py` |

Expected size: 5 new modules (hosts about 200 lines, link 220, sync 200, agent 150), edits
to `wrap.py`, `dispatch.py`, `runs.py`, `apply.py`, `project.py`, `check.py`,
`skills/dev-loop/SKILL.md`, `PLAYBOOK.md`, `project.example.json`; tests about 600 lines.
New public names: `select_host`, `HostCfg`, `probe`, `ssh_argv`, `remote_alive`,
`kill_remote`, `push_head`, `fetch_back`, `copy_back`, `sync_refs`,
`runs.reservation_keys`, `devloop-state host-check`.

## Established solutions

Search run 2026-11-25: "Ansible async_wrapper job status file poll" and "kill remote
process when ssh connection drops stdin EOF watchdog". Found:
- Ansible async: the remote job writes a JSON status file (`~/.ansible_async/`) with rc and
  output, and the controller polls it; an unreadable file reads as still running
  (docs.ansible.com, playbooks_async and async_status). The agent's `pid.json` and `exit`
  files follow this.
- Remote commands survive a dropped ssh connection by default: the remote child is
  reparented to init (agwa.name, "remote ssh commands and broken connections"). Fixes in
  use: a stdin-EOF watchdog (Baeldung, "kill remote process when ssh terminates"), Linux
  `prctl(PR_SET_PDEATHSIG)` (Red Hat), and `ssh -tt` for SIGHUP. The plan uses the stdin
  watchdog, which also works on macOS where `prctl` does not exist, and not `-tt`, which
  mangles the byte stream.
- OpenSSH `ControlMaster`/`ControlPersist` multiplexing for the 15 s polls, and
  `ServerAliveInterval` for local detection of a dead link (ssh_config(5)).
- In-repo: `~/bin/vm-run` pushes refs to a host's bare mirror and runs the job over ssh
  (lines 124 to 127), and documents that a killed local `vm-run` leaves the remote job
  running (NODE-DIRECTORY.md, Dell gotchas). This plan closes that gap for workers.
Custom code left: the agent (about 150 lines) and the host chooser. Both are small and have
no maintained stdlib equivalent; a job system (Slurm, HTCondor, Buildkite agents) is far
heavier than three hosts need.

## Reuse inventory

- `~/bin/vm-run`: ssh options (`BatchMode`, `ConnectTimeout`), push to a bare mirror,
  stall guard idea. Not called; patterns copied into `link.py` and `sync.py`.
- Node directory page (`nodes.directory`): host facts, reach commands, Dell crash and
  reboot rules, Mac `ulimit` note. Read by the owner during setup and named in briefs.
- `runs.proc_start`, `runs.boot_id`, `runs._alive`, `holds_slot`, `writer_runs`,
  `kill_payload`, `remove_tree`: the local liveness model, mirrored for remote pids.
- `wrap._watch`, `wrap.finish`, `wrap._write_result`: unchanged; the remote path feeds
  them a local process (the ssh client) and local files.
- `apply.collect` and `release`: extended with the remote confirmation, not replaced.
- OOM fix: `projmod.memory_mb`, `_check_memory` reservation window, `mcp_args`.
- `store.write_json`, `locked`, `register_request`: unchanged.

## Frozen contracts

- `request.json` stays frozen at admission and hashed in the ledger. New fields: `host`
  (`local` or a host name) and `remote` (`{workdir, wt, out, mirror}` or null).
- `exit` reasons gain `unconfirmed`, `link-lost`, `setup` and `fetch-failed`; readers that
  switch on reasons get a row each (Routes).
- `KINDS`, verdict readers, `result.md` header keys, apply rules: unchanged.
- `devloop-state check` format `ok|FAIL: message`.
- Existing local runs behave exactly as today when `hosts` is absent from `project.json`.

## Enumerations

### Exit paths

Every way a remote run ends and what records it (local path in `devloop/wrap.py:241`):
- Payload exits: agent writes remote `exit`; ssh returns; wrapper copies back, fetches,
  `finish` writes local `exit` with reason `exit`.
- Deadline or idle: the wrapper kills the remote group through ssh, confirms, reason
  `deadline` or `hung` (idle from remote log size, polled over the multiplexed link).
- ssh drops and the host answers on reconnect: kill and confirm, copy back what exists,
  reason `link-lost`, counted as a failure with one retry.
- ssh drops and the host does not answer: reason `unconfirmed`; slot and reservation held
  (`devloop/runs.py:216`) until `collect` confirms death or the owner releases.
- Devbox wrapper killed (reboot, OOM): no local `exit`. `collect` (`devloop/apply.py:43`)
  sees a dead wrapper and, for a remote run, runs the remote confirmation before writing
  `lost`; unconfirmed stays held.
- Pre-run step fails (push, rsync, mirror, worktree add): reason `setup`, no payload
  started, slot freed, no failure count against the slice (infra like a test's 255).
- Fetch-back fails: reason `fetch-failed`, status FAILED, remote worktree kept, problem
  `remote-commits-unfetched` raised; the next dispatch for the slice fetches first.
- Usage cap in the remote log: `_set_cap_until` reads the copied log, as today.

### Routes

Run kinds and where they may run (one table, `remote.hosts.REMOTE_KINDS`):
- `builder` (Developer): remote in S1 (Dell only, max 3), writes the slice worktree.
- `planner` (Architect), `docs` (Tech writer): remote from S2, write the slice worktree.
- `dennis` (Senior reviewer): remote from S2; pinned private checkout made on the host from
  the mirror at the frozen head (`devloop/dispatch.py:444` today makes it locally).
- `codex-builder`, `codex-review`, `codex-fix`, `test`, `duty`: local only; an attempt to
  configure them in a host's `roles` is refused at config load.
- Exit reasons route to `parse_result` as today; `unconfirmed` holds like an unconfirmed
  test; `setup` and `link-lost` map to FAILED with the infra or retry rule.

### FK shapes

References that cross the host boundary, each checked:
- run `host` to `project.hosts[name]`: unknown host at apply is a rejection.
- run `root` and branch key to other live writers (any host): admission refuses a match.
- slice head to a commit in the devbox repo after fetch-back: `resolve_head`
  (`devloop/slices.py:452`) must succeed before apply credits a builder run.
- remote `pid.json` (pid, start, boot id) to the live process on that host: kill only on a
  match.
- `path_map` entries to files that exist on the host: checked by `host-check`.

### Fields

`project.json` `hosts{name: {ssh, roles[kind], max{kind: n}, memory_mb_free_min, workdir,
path_map{local: remote}, python, plugin_root}}`; `ssh` is an ssh destination string;
`workdir` absolute; `roles` subset of `REMOTE_KINDS`; `max` per listed kind, n >= 0.
- `request.json` adds `host`, `remote{workdir, wt, out, mirror}`.
- `proc.json` adds `remote_pid`, `remote_start`, `remote_boot`.
- `hosts-cache.json`: `{name: {ok, mem_free_mb, boot, checked_at}}`, devbox clock.
- Agent files on the host, in the remote out dir: `brief.md`, `pid.json`, `log`, `exit`,
  `result.md`, `final.md`.

### Output surfaces

- Local run dir: `exit` with the new reasons, copied `out/` files, `wrap.log` (ssh stderr
  first 2000 chars, no env values).
- Events: `run-host` (run, host), `remote-unconfirmed`, `remote-released`, `fetch-failed`.
- Known problems: `remote-commits-unfetched`, `host-unreachable-<name>`.
- STATUS and the snapshot: per-host running counts and host health from the cache.
- `devloop-state check` and `host-check`: one line per host per item.
- Slack via the existing tick line: host counts only, no host addresses.

## Defect classes

| # | Class | Closing structure | Sentry or property test | Mutation that must fail it |
|---|---|---|---|---|
| 1 | value-free output | ssh stderr and remote logs never enter events, Slack or STATUS; check lines carry names and ok only | `test_remote_errors_stay_in_run_dir`, `test_host_check_lines_have_no_output_text` | append ssh stderr to the `fetch-failed` event |
| 2 | cleanup on every exit path | agent kills on stdin EOF and at its own deadline; wrapper kills on reconnect; `unconfirmed` holds until confirmed | `test_ssh_drop_kills_remote_group`, `test_agent_self_deadline_kills`, `test_unreachable_host_holds_slot` | skip `kill_remote` in the reconnect branch |
| 3 | state consistency | one admission lock picks host and reserves keys; request frozen with host before launch | `test_concurrent_dispatch_no_double_host_slot`, `test_request_host_in_ledger_hash` | read host counts outside the admission lock |
| 4 | route completeness | `REMOTE_KINDS` table; every exit reason has a reader row | `test_every_exit_reason_routed`, `test_codex_kinds_refused_in_host_roles` | delete `unconfirmed` from the holds-slot reasons |
| 5 | referential integrity | builder credit needs the fetched head to resolve on the devbox; unknown host rejected at apply | `test_apply_refuses_unfetched_head`, `test_unknown_host_rejected` | credit the head from `result.md` without fetch-back |
| 6 | untrusted input | `pid.json`, remote `exit` and copied files parsed strictly; a damaged file reads as unknown, never as dead | `test_corrupt_remote_pid_reads_unconfirmed`, `test_copy_back_size_capped` | treat an unreadable `pid.json` as "process gone" |
| 7 | TOCTOU (check then use) | kill only after re-reading pid and start time in the same ssh command; fast-forward-only push and merge | `test_kill_skips_reused_remote_pid`, `test_push_refuses_non_ff` | kill by pid without the start-time check |
| 8 | weak tests | each mutation above added to `tests/devloop/mutations.sh` | `mutations.sh` reports every listed mutation killed | make `reservation_keys` drop the branch key (caught by `test_two_hosts_same_branch_refused`) |
| 9 | refactor drift | `req["root"]` built only by `reservation_keys`; legacy roots read as `local:` | existing `test_runs_dispatch.py` admission tests unchanged and green, `test_legacy_root_reads_local` | compare raw `root` strings in `writer_runs` |
| 10 | frozen contracts | ledger hash, `result.md` keys and apply unchanged; no `hosts` key means today's local path | `test_no_hosts_config_is_byte_identical_request`, existing `test_runs_apply.py` | add `host` to the request when `hosts` is absent |

## Acceptance tests

All in `tests/devloop/`, red first. A fake `ssh` in `fakebin/` runs the remote command
locally under a per-host fake root (`$FAKE_SSH_ROOT/<host>`), can drop the link
(`FAKE_SSH_DROP_AFTER`), can refuse to connect (`FAKE_SSH_DOWN`), and can report a new boot id
(`FAKE_SSH_BOOT`). The real agent runs under it. No network.
- `test_remote_builder_round_trip`: dispatch picks the fake Dell, the payload commits in the
  remote worktree and writes `result.md`, the wrapper copies back and fetches, the devbox
  worktree fast-forwards, `apply` credits the head.
- `test_ssh_drop_kills_remote_group`: drop the link mid-run; the payload group is gone, exit
  reason `link-lost`.
- `test_unreachable_host_holds_slot`: drop and go down; exit `unconfirmed`; slot and both
  reservation keys held; bring the host back; `collect` confirms and writes `released`.
- `test_reboot_boot_id_confirms_dead`.
- `test_agent_self_deadline_kills` (agent alone, short deadline).
- `test_kill_skips_reused_remote_pid`.
- `test_two_hosts_same_branch_refused`, `test_same_worktree_path_two_hosts_allowed_for_readers`.
- `test_select_host_order` (affinity, then most free memory, then local),
  `test_select_host_memory_floor`, `test_select_host_max_per_kind`,
  `test_select_host_falls_back_to_local_when_all_down`.
- `test_probe_cache_60s` (one ssh probe per host per minute).
- `test_mac_memory_from_vm_stat` (fixture output).
- `test_apply_refuses_unfetched_head`, `test_fetch_failed_keeps_remote_worktree`.
- `test_push_refuses_non_ff` (remote mirror ahead: refused with `remote-commits-unfetched`).
- `test_payload_env_has_no_github_auth` (agent records env keys; `GH_TOKEN` empty,
  `GH_CONFIG_DIR` under workdir, `DEVLOOP_HEADLESS=1`).
- `test_brief_paths_mapped_for_host`.
- `test_host_check_lines` (ssh, python, claude version, plugin listed, merge-guard probe
  denies `gh pr merge 1`, rules-gate probe denies an unread write, mirror reachable, free
  disk at least 10 GB, no GitHub auth for the run env).
- `test_no_hosts_config_is_byte_identical_request`.
- `test_no_private_strings_in_shipped_files` covers `devloop/remote/*`.
- Drills on real hosts before S1 widens (proof criteria below), recorded in the run log.

## Host setup procedure

Owner steps, documented in `skills/dev-loop/SKILL.md` "hosts", checked by
`devloop-state PROJECT host-check HOST`:
1. ssh key from the devbox to the host with `BatchMode` working (exists for both hosts).
2. Install Node and the claude CLI on the host. Python 3.9 or later on PATH.
3. Credentials (owner only; flagged): run `claude login` on the host, or put
   `ANTHROPIC_API_KEY` in `~/.config/devloop/env` on the host, mode 0600, which the agent
   sources. The devbox never sends, stores or logs a key. Rotation and revocation stay with
   the owner's account settings.
4. Install the dev-loop plugin on the host and add `<workdir>/ref/rules` to its
   `rules_dirs`.
5. No GitHub step. Worker hosts need no GitHub credentials; this replaces "clone the repos
   and set up git push credentials". `host-check` creates the bare mirrors by push.
6. Project toolchain for builders (for example `uv` and the repo's Python): the project's
   own `hosts[name].prepare` argv, run in the worktree before the payload, with a 10 min
   deadline. Optional.
7. Recommended, owner decision: GitHub branch protection on base branches as a third layer
   behind the hooks and the no-credentials rule.

## Slices

| Slice | Tasks | Ships | Gate |
|---|---|---|---|
| S1 Developer on the Dell | 1, 2, 3, 4, 5, 6 | host config and choice, agent, link and kill, git sync, wrapper and collect, host-check, Dell `builder` max 3 | Senior reviewer then Codex on the S1 head; T1 = `pytest tests/devloop`; T2 with `mutations.sh`; drills below |
| S2 widen | 7 | `planner`, `docs`, pinned `dennis` on the Dell; then the Mac for all four | Codex on the S2 head; drills repeated on the Mac |

Proof criteria before S2 starts (all recorded in the run log and the tick log):
- 10 remote builder runs applied on the Dell with no apply rejection caused by the remote
  path, and every builder head resolved on the devbox.
- Link-drop drill: kill the local ssh mid-run 3 times; 0 remote payloads alive after 5 min.
- Dell reboot drill (owner-approved reboot under the node directory's standing rule): the
  run reads `unconfirmed`, then `released` by boot id; reservations freed; no lost commit.
- Devbox memory: no OOM and `MemAvailable` above the floor for 3 days with 3 remote
  builders plus local reviewers.
- `host-check` green daily for 3 days.
A failed drill blocks widening and becomes a known problem with a fix slice.

## Pre-mortem

- **ssh flaps.** ServerAlive detects within about 60 s; reconnect 5 times; the agent's stdin
  watchdog kills on clean link loss; held reservations stop a second writer meanwhile.
- **Dell reboot or crash** (8 crashes 11-17 to 11-21 per the node directory). Remote runs go
  `unconfirmed` and hold; boot id change confirms death; the remote worktree survives on
  disk; fetch-first on the next dispatch recovers unfetched commits. The host drops out of
  `select_host` until its probe answers.
- **WSL clock skew.** No cross-host time comparison: devbox clock for every age, remote
  start times compared only with the same host's values.
- **Git credentials missing.** Not needed on hosts. Devbox push to the mirror uses the
  existing ssh key; failure is `setup`, so no slice failure is counted.
- **Remote disk full.** `host-check` and the probe read free disk; under 10 GB the host is
  skipped at admission; a payload failing on disk ends FAILED with its log copied back;
  worktrees of DONE slices are removed by the existing cleanup rule only when fully fetched.
- **Dell CPU saturated by tests.** Builders run slower; the idle limit counts log growth,
  not CPU; deadline unchanged. If deadlines start firing, add a load gate as a follow-up.
- **Hooks missing on a host after a plugin update.** `host-check` runs at each tick start
  for hosts in use (cached 1 h); a failed probe removes the host from selection.
- **Mac IP changes (no DHCP reservation).** Probe fails, host skipped, `host-unreachable`
  problem raised; owner fixes the reservation.

## Task 1: Host config and selection

**Files:** create `devloop/remote/__init__.py`, `devloop/remote/hosts.py`; modify
`devloop/project.py`, `skills/dev-loop/project.example.json`; test
`tests/devloop/test_remote_hosts.py`.

**Interfaces:** `HostCfg(name, ssh, roles, max, memory_mb_free_min, workdir, path_map,
python, plugin_root, prepare)`; `REMOTE_KINDS = ("builder", "planner", "docs", "dennis")`;
`probe(project, host) -> dict` (cached 60 s in `hosts-cache.json`; Linux `MemAvailable`,
macOS `vm_stat` free plus inactive plus speculative pages); `select_host(project, cfg, req)
-> str` called under the admission lock; `project.py` validates `hosts` with key paths.

- [ ] Step 1: failing `test_select_host_*`, `test_probe_cache_60s`,
  `test_mac_memory_from_vm_stat`, `test_codex_kinds_refused_in_host_roles`.
- [ ] Step 2 to 4: red, implement, green.
- [ ] Step 5: commit `feat(devloop): add worker hosts and host selection`.

## Task 2: Agent

**Files:** create `devloop/remote/agent.py`; test `tests/devloop/test_remote_agent.py`.

**Interfaces:** `devloop-agent run <out_dir> <deadline_s> -- <argv...>`: reads the brief
from stdin until a NUL line, writes `brief.md`, starts the payload in a new session with the
brief as stdin and `log` as output, writes `pid.json` (pid, start, boot), watches stdin for
EOF in a thread, kills the group on EOF or at `deadline_s + 600`, writes `exit`.
`devloop-agent status <out_dir>` prints alive or dead by pid and start. `devloop-agent kill
<out_dir>` kills only on a start-time match. Start time from `/proc/<pid>/stat` or
`ps -o lstart=`; boot from `/proc/sys/kernel/random/boot_id` or `sysctl -n kern.boottime`.

- [ ] Step 1: failing `test_agent_self_deadline_kills`, EOF-kill, status and kill tests.
- [ ] Step 2 to 4: red, implement, green. Step 5: commit `feat(devloop): add the remote worker agent`.

## Task 3: Link and liveness

**Files:** create `devloop/remote/link.py`, `tests/devloop/fakebin/ssh`; test
`tests/devloop/test_remote_link.py`.

**Interfaces:** `ssh_argv(host, *cmd)`; `start(host, req, brief_bytes) -> Popen` (stdin kept
open); `remote_alive(host, req) -> True | False | None` (None means no answer);
`kill_remote(host, req) -> bool`; `reconnect_and_kill(host, req) -> bool`. Guard test: no
other `"ssh"` argv literal in `devloop/`.

- [ ] Steps: red (`test_ssh_drop_kills_remote_group`, `test_kill_skips_reused_remote_pid`,
  `test_corrupt_remote_pid_reads_unconfirmed`, guard), implement, green. Commit
  `feat(devloop): supervise remote workers over ssh`.

## Task 4: Git and file sync

**Files:** create `devloop/remote/sync.py`; test `tests/devloop/test_remote_sync.py`.

**Interfaces:** `ensure_mirror(host, repo)`; `push_head(host, s, req)` (fast-forward only;
remote ahead raises `RemoteAhead`); `ensure_worktree(host, req)`; `sync_refs(host, cfg)`
(rsync read-only); `copy_back(host, req, rd)` (5 MB log cap); `fetch_back(host, req, s)`
(fetch, then `merge --ff-only` in the devbox worktree, refusing a dirty tree).

- [ ] Steps: red (`test_push_refuses_non_ff`, `test_fetch_failed_keeps_remote_worktree`,
  `test_copy_back_size_capped`, `test_brief_paths_mapped_for_host`), implement, green.
  Commit `feat(devloop): move commits and results between hosts`.

## Task 5: Dispatch, wrapper, collect

**Files:** modify `devloop/dispatch.py`, `devloop/wrap.py`, `devloop/runs.py`,
`devloop/apply.py`; test `tests/devloop/test_remote_runs.py`.

**Interfaces:** `runs.reservation_keys(req) -> tuple[str, str | None]`; admission reserves
both keys and calls `select_host`; `_freeze` records `host` and `remote`, builds the brief
with the host's `path_map`; `wrap.run` branches once on `req["host"]`: local as today,
remote through `link.start` with `_watch` on the ssh client and the remote log size, then
`copy_back`, `fetch_back`, `finish`. `collect` confirms remote death before `lost` and
writes `released` for confirmed `unconfirmed` runs. Payload env as in Design (Hooks).

- [ ] Steps: red (`test_remote_builder_round_trip`, `test_unreachable_host_holds_slot`,
  `test_reboot_boot_id_confirms_dead`, `test_two_hosts_same_branch_refused`,
  `test_apply_refuses_unfetched_head`, `test_payload_env_has_no_github_auth`,
  `test_no_hosts_config_is_byte_identical_request`, `test_legacy_root_reads_local`,
  `test_every_exit_reason_routed`), implement, green. Commit
  `feat(devloop): run Developer workers on remote hosts`.

## Task 6: Host check, skill, mutations

**Files:** modify `devloop/check.py`, `devloop/state_cli.py`, `skills/dev-loop/SKILL.md`,
`skills/dev-loop/PLAYBOOK.md`, `tests/devloop/mutations.sh`, `test_shipped_files.py`.

`host-check` lines: ssh, python version, claude version, plugin listed, merge-guard probe
(the host's `run-hook.sh headless_merge_guard` given a PreToolUse event for `gh pr merge 1`
with `DEVLOOP_HEADLESS=1` must deny), rules-gate probe (an Edit event for a source file in
a fresh transcript must deny), mirror reachable, free disk at least 10 GB, the run env has
no GitHub auth (`GH_CONFIG_DIR` empty dir, `gh auth status` fails). `check` runs it per
configured host. SKILL.md gets the setup procedure above; PLAYBOOK gets the scenario rows
for `unconfirmed`, `fetch-failed` and `setup`.

- [ ] Steps: red (`test_host_check_lines`, shipped-files coverage), implement, green; run
  `mutations.sh`, all remote mutations killed; run the S1 drills on the Dell. Commit
  `feat(devloop): check worker hosts and document setup`.

## Task 7: Widen (S2, after the proof criteria hold)

**Files:** modify `devloop/remote/hosts.py`, `devloop/dispatch.py` (pinned checkout on the
host from the mirror at the frozen head, removed by `remove_tree` through ssh), the
project's `hosts` config.

- [ ] Steps: red (`test_remote_dennis_pinned_checkout`, `test_remote_tree_removed_on_exit`,
  `test_remote_planner_writes_plan_path`), implement, green; drills on the Mac. Commit
  `feat(devloop): run Architect, Tech writer and Senior reviewer remotely`.
