#!/usr/bin/env bash
# Apply each mutation to a temp copy of the repo, run the tests that guard it, and expect
# a failure. A mutation whose tests still pass is a survivor. Usage: tests/devloop/mutations.sh
# The edits are exact-text replacements (a stale edit fails the script, so none can rot).
set -uo pipefail
# A same-size edit within one second would otherwise leave a stale mutated .pyc behind.
export PYTHONDONTWRITEBYTECODE=1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$ROOT/devloop" "$ROOT/hooks" "$ROOT/bin" "$ROOT/skills" "$ROOT/tests" "$ROOT/conftest.py" "$ROOT/pyproject.toml" "$WORK/"
find "$WORK" -name __pycache__ -prune -exec rm -rf {} +
killed=0; survived=0; stale=0; redbase=0
declare -a SURVIVORS

# mutate NAME FILE OLD NEW TESTS...
mutate() {
  local name="$1" file="$2" old="$3" new="$4"; shift 4
  if ! (cd "$WORK" && "$PY" -m pytest -q -x -p no:cacheprovider "$@" >/dev/null 2>&1); then
    echo "RED-BASE $name (tests fail before the edit)"; redbase=$((redbase+1)); return
  fi
  cp "$WORK/$file" "$WORK/$file.orig"
  if ! OLD="$old" NEW="$new" F="$WORK/$file" "$PY" - <<'PYEOF'
import os, sys
p = os.environ["F"]; t = open(p).read(); old = os.environ["OLD"]
if old not in t:
    sys.exit(1)
open(p, "w").write(t.replace(old, os.environ["NEW"], 1))
PYEOF
  then
    echo "STALE   $name (text not found in $file)"; stale=$((stale+1)); mv "$WORK/$file.orig" "$WORK/$file"; return
  fi
  # A kill is pytest exit 1 (a failed assertion). Exit 2 to 5 is a collection, usage or
  # setup error, which proves nothing about the tests.
  (cd "$WORK" && "$PY" -m pytest -q -x -p no:cacheprovider "$@" >/dev/null 2>&1)
  rc=$?
  if [ "$rc" -eq 1 ]; then
    echo "killed   $name"; killed=$((killed+1))
  else
    echo "SURVIVED $name (pytest exit $rc)"; survived=$((survived+1)); SURVIVORS+=("$name")
  fi
  mv "$WORK/$file.orig" "$WORK/$file"
}

T=tests/devloop
mutate "public text carries raw next_action" devloop/status.py \
  'return f"{s['"'"'row'"'"']} {s['"'"'slice'"'"']} in {s['"'"'phase'"'"']}"' \
  'return str(s["next_action"] or "")' \
  $T/test_tick.py::test_public_outputs_use_event_fields
mutate "wrapper never pushes the writer's commits" devloop/push.py \
  '        pushed = _git(wt, "push", "-q", "origin", f"{local}:{ref}")' \
  '        pushed = _git(wt, "status", "-q")' \
  $T/test_push.py::test_unpushed_commit_is_pushed_and_applied
mutate "wrapper drops the exit-writing finally step" devloop/wrap.py \
  '        finish(project, rd, req, code, reason)' '        pass' \
  $T/test_wrap.py::test_wrapper_writes_exit_on_kill
mutate "processed run id is never recorded" devloop/slices.py \
  '        s["processed_run_ids"] = (s["processed_run_ids"] + [run_id])[-500:]' '        pass' \
  $T/test_runs_apply.py::test_apply_replay_counts_once
mutate "KINDS loses the test entry" devloop/runs.py \
  '    "test": Kind("tester", 7200, 1200, "testexit", False, True, "script", "none"),' '' \
  $T/test_runs_dispatch.py::test_every_kind_has_reader_and_slot
mutate "evidence key from the slice phase at apply time" devloop/apply.py \
  "ev_kind = f\"{req['purpose']}:{req['reviewer']}\"" \
  "ev_kind = f\"{'plan' if s['phase'] == 'PLAN' else 'code'}:{req['reviewer']}\"" \
  $T/test_runs_apply.py::test_late_plan_review_grants_no_code_go
mutate "verdict taken from anywhere in the transcript" devloop/wrap.py \
  'm = rx.fullmatch(_plain_line(lines[-1])) if lines else None' \
  'm = next((x for x in map(rx.fullmatch, map(_plain_line, lines)) if x), None)' \
  $T/test_wrap.py::test_verdict_must_be_the_last_line_of_the_final_message $T/test_wrap.py::test_go_with_fixes_is_not_go
mutate "an error result can carry the verdict" devloop/wrap.py \
  'final = None if event.get("is_error") else event.get("result")' \
  'final = event.get("result")' \
  $T/test_wrap.py::test_error_result_cannot_supply_a_verdict
mutate "admission lets one past the cap" devloop/dispatch.py \
  'if used >= cfg["capacity"][spec.slot]:' 'if used > cfg["capacity"][spec.slot]:' \
  $T/test_runs_dispatch.py::test_admission_refuses_ninth_builder $T/test_runs_dispatch.py::test_admission_refuses_fifth_tester
mutate "merge drops --match-head-commit" devloop/merge.py \
  '        "--match-head-commit",
        s["head"],' '' \
  $T/test_merge.py::test_merge_success_moves_to_document
mutate "PR head is never compared" devloop/merge.py \
  'return (
        isinstance(oid, str)
        and bool(head)
        and (oid == head or (len(head) >= 7 and oid.startswith(head)))
    )' 'return True' \
  $T/test_merge.py::test_merge_refuses_head_mismatch
mutate "an unknown merge state is retried" devloop/merge.py \
  'if attempt or s["merge_unknown"]:' 'if False:' \
  $T/test_merge.py::test_merge_unknown_state_no_retry
mutate "merge guard forgets gh pr merge" hooks/headless_merge_guard.py \
  '            if _gh_blocked(args, text):' '            if False:' \
  $T/guard/test_merge_guard.py::test_merge_guard_denies_in_headless_mode
mutate "merge guard ignores a subagent id" hooks/headless_merge_guard.py \
  'worker = in_run or bool(event.get("agent_id"))' 'worker = in_run' \
  $T/guard/test_merge_guard.py::test_subagent_is_not_the_orchestrator
mutate "merge guard blocks every git push" hooks/headless_merge_guard.py \
  '            if _push_targets_base(" ".join(rest), root):' '            if True:' \
  $T/guard/test_merge_guard.py::test_merge_guard_allows_reads_feature_pushes_and_devloop_merge
mutate "merge guard blocks any text with merge" hooks/headless_merge_guard.py \
  '        elif exe == "git" and check_base_pushes:' '        elif "merge" in text:
            return True
        elif exe == "git" and check_base_pushes:' \
  $T/guard/test_merge_guard.py::test_merge_guard_allows_reads_feature_pushes_and_devloop_merge
mutate "request ledger always matches" devloop/store.py \
  'return want is not None and want == request_digest(run_dir(project, run_id))' 'return True' \
  $T/test_runs_integrity.py::test_payload_that_rewrites_request_gets_no_evidence
mutate "repaired copy is not re-hashed" devloop/apply.py \
  'if runs.sha_file(copy) != data.get("output_sha"):' 'if False:' \
  $T/test_runs_integrity.py::test_tampered_repaired_copy_is_caught_before_install
mutate "a lost test run frees its slot" devloop/runs.py \
  '    return (rd / "released").is_file() or (' '    return True or (' \
  $T/test_runs_apply.py::test_lost_test_holds_slot
mutate "a reused pid reads alive" devloop/runs.py \
  'return proc_start(pid) == str(start)' 'return proc_start(pid) is not None' \
  $T/test_runs_apply.py::test_reused_pid_reads_dead
mutate "zero exit without a result reads DONE" devloop/apply.py \
  'if status not in runs.STATUSES:
        status = "FAILED"' 'if status not in runs.STATUSES:
        status = status or "DONE"' \
  $T/test_runs_apply.py::test_zero_exit_without_result_is_failed
mutate "skip check ignores the unchanged hash" devloop/tick.py \
  '        digest == state.get("last_hash")' '        False' \
  $T/test_tick.py::test_skip_unchanged_snapshot_until_two_hours
mutate "skip never expires" devloop/tick.py \
  '        < SKIP_WINDOW_S
    )' '        < SKIP_WINDOW_S * 1000
    )' \
  $T/test_tick.py::test_skip_unchanged_snapshot_until_two_hours
mutate "a timeout counts as a cap" devloop/caps.py \
  '    if rc == 124:
        return None' '    if False:
        return None' \
  $T/test_tick.py::test_detect_cap_shapes
mutate "codex cap text is ignored" devloop/caps.py \
  '    if CAP_TEXT.search(tail) and TRY_AGAIN.search(tail):' '    if False:' \
  $T/test_wrap.py::test_codex_usage_cap_text_sets_cap_until
mutate "a rejected run still holds its slot" devloop/runs.py \
  '    if (rd / "rejected").is_file():
        return False
    if spec.writes_worktree' '    if spec.writes_worktree' \
  $T/test_runs_integrity.py::test_ledger_mismatch_rejects_the_run_and_frees_the_slot $T/test_runs_integrity.py::test_repaired_hash_mismatch_rejects_the_run_and_frees_the_worktree
mutate "a rejected run is still collected" devloop/apply.py \
  '            if (rd / "exit").is_file() and not runs.is_done(rd):' '            if (rd / "exit").is_file() and not (rd / "applied").is_file():' \
  $T/test_runs_integrity.py::test_ledger_mismatch_rejects_the_run_and_frees_the_slot
mutate "refusal writes no rejected marker" devloop/apply.py \
  '    (rd / "rejected").write_text(why)' '    pass' \
  $T/test_runs_integrity.py::test_binding_mismatch_rejects_the_run_and_frees_the_slot
mutate "held test runs are not listed" devloop/snapshot.py \
  '            and runs.holds_slot(rd, req)' '            and False' \
  $T/test_snapshot_status.py::test_snapshot_lists_test_runs_that_hold_a_slot
mutate "remove_tree trusts the request path" devloop/runs.py \
  '    tree = rd / "tree"
    if not tree.exists()' '    tree = Path((req or {}).get("tree") or rd / "tree")
    if not tree.exists()' \
  $T/test_runs_integrity.py::test_remove_tree_ignores_a_path_named_in_the_request
mutate "builder failures keyed by head" devloop/apply.py \
  '        base = f"{kind}:{req.get('"'"'lineage'"'"')}"' '        base = f"{kind}:{req.get('"'"'head'"'"')}"' \
  $T/test_runs_integrity.py::test_builder_failures_count_across_new_heads
mutate "markdown is not stripped from the verdict" devloop/wrap.py \
  '    m = rx.fullmatch(_plain_line(lines[-1])) if lines else None' '    m = rx.fullmatch(lines[-1]) if lines else None' \
  $T/test_wrap.py::test_markdown_around_the_verdict_line_is_stripped
mutate "a closed PR is read as unknown" devloop/merge.py \
  '    return {"OPEN": "open", "CLOSED": "closed"}.get(state, "unknown")' '    return {"OPEN": "open"}.get(state, "unknown")' \
  $T/test_merge.py::test_closed_pr_clears_the_flags_and_refuses_with_a_fixed_reason
mutate "merge move re-checks the gate" devloop/slices.py \
  '            if via_merge
            else [i.label' '            if False
            else [i.label' \
  $T/test_merge.py::test_plan_edited_after_the_merge_still_finishes_the_move
mutate "no-verdict resumes forever" devloop/apply.py \
  'reason == "no-verdict" and count < 3' 'reason == "no-verdict"' \
  $T/test_runs_integrity.py::test_third_no_verdict_after_a_resume_is_owner_gated
mutate "a red test is a failed run" devloop/apply.py \
  '        if not red_test:' '        if True:' \
  $T/test_runs_apply.py::test_red_test_rerun_replaces_an_earlier_go
mutate "a selection earns t2 or full" devloop/dispatch.py \
  '    if select and level in ("t2", "full"):' '    if False:' \
  $T/test_runs_apply.py::test_selection_is_refused_for_t2_and_full_but_not_t1
mutate "guard ignores configured bases" devloop/project.py \
  '    names = {"main", "master"}' '    return "main,master"
    names = {"main", "master"}' \
  $T/test_tick.py::test_tick_session_gets_the_configured_base_branches
mutate "run conclusion compared case-sensitively" devloop/merge.py \
  '    return isinstance(conclusion, str) and conclusion.lower() == "success"' '    return conclusion == "SUCCESS"' \
  $T/test_merge.py::test_merge_refuses_gate_dispatch_before_codex_go
mutate "unknown merge drops the attempt" devloop/merge.py \
  '    slices.set_merge_state(project, key, unknown=True)' '    slices.set_merge_state(project, key, attempt=None, unknown=True)' \
  $T/test_merge.py::test_unknown_response_keeps_the_attempt
mutate "board attempt is not saved before the call" devloop/board.py \
  '        rec["board_attempted"] = True' '        pass' \
  $T/test_merge.py::test_a_crash_between_the_board_call_and_board_done_does_not_post_twice
mutate "merge identity not checked" devloop/slices.py \
  '                if (s["repo"], s["pr"], s["head"]) != (' '                if False and (s["repo"], s["pr"], s["head"]) != (' \
  $T/test_merge.py::test_finish_refuses_when_the_slice_no_longer_matches_the_confirmed_merge
mutate "up-to-date skips the ancestor check" devloop/merge.py \
  '    if not _git_ok(repo_cfg, "merge-base", "--is-ancestor", f"origin/{base}", head):' '    if False:' \
  $T/test_merge.py::test_up_to_date_needs_the_base_tip_in_the_head
mutate "finished runs do not force a pass" devloop/tick.py \
  '        and not snapshot.finished_unapplied(project)' '        and True' \
  $T/test_tick.py::test_finished_unapplied_run_forces_a_model_pass_whatever_the_hash
mutate "exit is written in place" devloop/apply.py \
  '        os.link(tmp, rd / "exit")' '        os.rename(tmp, rd / "exit")' \
  $T/test_runs_integrity.py::test_exit_is_published_whole_or_not_at_all
mutate "damaged exit is never recovered" devloop/apply.py \
  '            if damaged and not runs.wrapper_alive(req):' '            if False:' \
  $T/test_runs_integrity.py::test_collect_recovers_a_damaged_exit_once_the_wrapper_is_dead
mutate "run view asks for a field gh lacks" devloop/merge.py \
  '        "event,headSha,workflowName,createdAt,conclusion,jobs",' '        "event,headSha,workflowName,path,createdAt,conclusion,jobs",' \
  $T/test_merge.py::test_workflow_file_name_is_resolved_through_the_actions_api
mutate "only exit 1 counts as a red test" devloop/apply.py \
  '            and ex.get("reason") == "exit"
        )
        if not red_test:' '            and ex.get("code") == 1
            and ex.get("reason") == "exit"
        )
        if not red_test:' \
  $T/test_runs_apply.py::test_red_test_rerun_replaces_an_earlier_go
mutate "gate ignores an unapplied rerun" devloop/evidence.py \
  '        if runs.is_done(rd) or req.get("kind") not in _EVIDENCE_KINDS:' '        if True:' \
  $T/test_merge.py::test_merge_refuses_while_a_rerun_for_the_head_is_unapplied
mutate "a pin hides a red check of the same name" devloop/merge.py \
  '            if id(e) not in satisfying and state(e) not in OK_STATES:' '            if e["name"] not in required and state(e) not in OK_STATES:' \
  $T/test_merge.py::test_merge_pinned_source_must_match
mutate "round seven still goes to remediate" devloop/slices.py \
  '            if s["round"] >= REDESIGN_AT:' '            if False:' \
  $T/test_runs_rounds.py::test_seventh_round_redesigns_the_plan_and_nothing_parks
mutate "builder allowed after round three" devloop/dispatch.py \
  '        if kind == "builder" and late:' '        if False:' \
  $T/test_runs_rounds.py::test_rounds_four_to_six_are_fixed_by_codex
mutate "an older run replaces newer evidence" devloop/slices.py \
  '    if seq is not None and prior is not None and (prior.get("seq") or 0) > seq:' '    if False:' \
  $T/test_runs_apply.py::test_an_older_green_cannot_replace_a_newer_red
mutate "plan exit ignores an unapplied review" devloop/evidence.py \
  '        if not runs.is_done(rd)
        and req.get("kind") in runs.REVIEW_KINDS' '        if False
        and req.get("kind") in runs.REVIEW_KINDS' \
  $T/test_runs_apply.py::test_plan_exit_refuses_while_a_plan_review_is_unapplied
mutate "redesign keeps old plan reviews" devloop/slices.py \
  '                s["plan_lineage"] += 1' '                pass' \
  $T/test_runs_rounds.py::test_redesign_needs_fresh_plan_reviews_even_after_an_in_place_rewrite
mutate "an ambiguous close is retried" devloop/board.py \
  '            if not rec.get("close_ambiguous"):' '            if False:' \
  $T/test_slices.py::test_close_attempt_without_a_saved_closure_is_ambiguous_and_never_retried
mutate "a row mate in DOCUMENT does not block the merge" devloop/evidence.py \
  '        if o["row"] == s["row"] and o["key"] != s["key"] and o["phase"] == "DOCUMENT"' '        if False' \
  $T/test_merge.py::test_a_slice_cannot_enter_gate_or_merge_while_a_row_mate_is_in_document
mutate "an old lineage builder run still counts" devloop/evidence.py \
  '        if req.get("plan_lineage", 0) != s["plan_lineage"]:' '        if False:' \
  $T/test_runs_rounds.py::test_redesign_with_an_unchanged_head_needs_a_new_builder_run
mutate "a failed close is read as not closed" devloop/board.py \
  '        elif state == "unstarted":' '        else:' \
  $T/test_slices.py::test_a_close_that_started_and_failed_or_timed_out_is_ambiguous
mutate "row-close ignores open slices" devloop/board.py \
  '    if any(p != "DONE" for p in phases):' '    if False:' \
  $T/test_slices.py::test_row_close_needs_every_slice_done_and_the_confirm_flag
mutate "tick line skips scrub" devloop/status.py \
  '    return scrub(head + body + "." + tail, LINE_LIMIT)' '    return head + body + "." + tail' \
  $T/test_tick.py::test_tick_slack_line_is_scrubbed
mutate "cron add keeps the old tagged line" devloop/cron.py \
  'if not line.rstrip().endswith(_tag(project))' 'if True' \
  $T/test_cron.py::test_cron_add_is_idempotent_and_keeps_others
mutate "cron read error counts as empty" devloop/cron.py \
  '        if "no crontab for" in r.stderr:
            return ""' '        if True:
            return ""' \
  $T/test_cron.py::test_cron_read_error_is_not_empty
mutate "hex runs are not scrubbed" devloop/scrub.py \
  '    for rx in (_PATH, _HEX):' '    for rx in (_PATH,):' \
  $T/test_snapshot_status.py::test_scrub_removes_sha_and_path
mutate "snapshot hash includes digits" devloop/snapshot.py \
  '"outputs": [(n, DIGITS.sub("", t)) for n, t, _ok in outputs],' '"outputs": [(n, t) for n, t, _ok in outputs],' \
  $T/test_snapshot_status.py::test_hash_ignores_ages_and_digits
mutate "stacked member need not be an ancestor" devloop/dispatch.py \
  '        if not stacked:' '        if False:' \
  $T/test_runs_apply.py::test_train_member_must_be_ancestor
mutate "codex review needs no earlier reviewer" devloop/dispatch.py \
  '    if i == 0:
        return None' '    if True:
        return None' \
  $T/test_runs_apply.py::test_codex_code_review_refused_before_dennis_go
mutate "a duplicate failing check name passes" devloop/merge.py \
  '        if not entries or any(state(e) != "SUCCESS" for e in entries):' '        if not entries or state(entries[0]) != "SUCCESS":' \
  $T/test_merge.py::test_merge_refuses_duplicate_name_with_one_failure
mutate "hold released by any choice" devloop/notes.py \
  'return s["hold_question"] == key and s["hold_release_option"] == choice' 'return True' \
  $T/test_notes.py::test_agent_question_never_lifts_a_hold

mutate "codex-written head skips the dennis review" devloop/evidence.py \
  '    if _claude_written(project, s):
        return roster' '    if True:
        return roster' \
  $T/test_runs_review_roster.py::test_r1_slice_fixed_by_codex_builder_needs_dennis_then_codex
mutate "tick starts workers with the user MCP servers" devloop/project.py \
  '    return ["--strict-mcp-config", "--mcp-config", path or NO_MCP]' '    return []' \
  $T/test_runs_limits.py::test_claude_workers_start_with_no_mcp_servers
mutate "memory admission ignores recent reservations" devloop/dispatch.py \
  '    held = runs.reserved_mb() + projmod.memory_mb(cfg, kind)' '    held = projmod.memory_mb(cfg, kind)' \
  $T/test_runs_limits.py::test_burst_of_dispatches_stops_at_the_reservation
mutate "tick dispatch limit is never enforced" devloop/dispatch.py \
  '    if made >= projmod.max_dispatch_per_tick(cfg):' '    if False:' \
  $T/test_runs_limits.py::test_tick_dispatch_limit

mutate "a word outside the fixed table is accepted" devloop/dash/view.py \
  '        if value not in WORDS:' '        if False:' \
  $T/test_dash_view.py::test_word_outside_the_table_is_refused
mutate "ids skip the hex check" devloop/scrub.py \
  '        or _HEX_RUN.search(text)
' '' \
  $T/test_dash_shared.py::test_token_in_id_is_redacted
mutate "the dashboard leaves its pid file behind" devloop/dash/lifecycle.py \
  '            _files(project)["pid"].unlink(missing_ok=True)' '            pass' \
  $T/test_dash_cli.py::test_sigterm_removes_pid_file
mutate "a reader creates the runs directory" devloop/runs.py \
  '    return store.state_path(project) / "runs"' '    d = store.state_path(project) / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d' \
  $T/test_dash_shared.py::test_readers_create_no_directories $T/test_dash_view.py::test_build_view_writes_nothing
mutate "error responses skip the security headers" devloop/dash/server.py \
  '    def send_error(self, code, message=None, explain=None):' '    def _unused(self, code, message=None, explain=None):' \
  $T/test_dash_server.py::test_every_response_sends_security_headers
mutate "a PR link is built from the ledger slug alone" devloop/dash/view.py \
  '    if slug in known and type(pr) is int and pr >= 0:' '    if type(pr) is int and pr >= 0:' \
  $T/test_dash_view.py::test_merge_with_unknown_slug_has_no_url
mutate "a failing checklist propagates" devloop/dash/view.py \
  '    except CHECKLIST_ERRORS:' '    except ZeroDivisionError:' \
  $T/test_dash_view.py::test_a_checklist_that_raises_reads_unavailable
mutate "a second dashboard is not refused" devloop/dash/lifecycle.py \
  '        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)' '        pass' \
  $T/test_dash_cli.py::test_second_start_same_project_other_port_refused
mutate "an exited builder reads as executing" devloop/runs.py \
  '    if not (rd / "exit").is_file() and (wrapper_alive(req) or payload_alive(req)):' '    if True:' \
  $T/test_dash_shared.py::test_exited_builder_is_awaiting_apply
mutate "a damaged cap file reads as not capped" devloop/store.py \
  '    except ValueError:
        return True
    if until.tzinfo is None:' '    except ValueError:
        return False
    if until.tzinfo is None:' \
  $T/test_dash_shared.py::test_cap_active_cases
mutate "a view key is renamed in the view only" devloop/dash/view.py \
  '        "phase_index": CHIPS.index(phase),' '        "phase_idx": CHIPS.index(phase),' \
  $T/test_dash_view.py::test_view_keys_match_schema
mutate "memory reads low only far below the floor" devloop/dash/memory.py \
  '    if headroom_mb < floor_mb:
        return "low"' '    if headroom_mb < floor_mb - 1000:
        return "low"' \
  $T/test_dash_view.py::test_memory_thresholds
mutate "an unchanged poll answers 200" devloop/dash/server.py \
  '    return any(t.strip().removeprefix("W/") == etag for t in header.split(","))' '    return False' \
  $T/test_dash_server.py::test_etag_304_on_unchanged_poll
mutate "the tick line names pools by kind" devloop/status.py \
  '        f"{projmod.pool_label(cfg, s)} {running.get(s, 0)}/{cfg['"'"'capacity'"'"'][s]}"' '        f"{s} {running.get(s, 0)}/{cfg['"'"'capacity'"'"'][s]}"' \
  $T/test_dash_shared.py::test_status_and_tick_line_use_role_labels
mutate "the renderer stops escaping" devloop/dash/render.py \
  'e = html.escape' 'e = str' \
  $T/test_dash_render.py::test_html_escapes_planted_markup
mutate "playbook heading needs an exact match only" devloop/dispatch.py \
  '    return heading == f"### {kind}" or (' '    return heading == f"### {kind}" and (' \
  $T/test_runs_dispatch.py::test_section_matches_the_plain_heading_and_the_kind_in_backticks

mutate "wrapper keeps the worker's head instead of the pushed one" devloop/wrap.py \
  '        store.rewrite_header(path, head=pushed)' '        pass' \
  $T/test_push.py::test_amended_head_is_pushed_and_reported
mutate "writer provenance is never recorded" devloop/slices.py \
  '                s["head_writer"] = writer' '                pass' \
  $T/guard/test_writer_provenance.py::test_apply_run_binds_kind_commit_and_lineage
mutate "a missing Senior reviewer no longer fails the gate" devloop/evidence.py \
  '    if senior_missing(project, cfg, s):' '    if False:' \
  $T/test_runs_review_roster.py::test_codex_written_head_without_a_senior_reviewer_fails_closed
mutate "merge reads the configured roster, not the effective one" devloop/merge.py \
  '    roster = evidence.code_roster(project, cfg, s)' \
  '    roster = [projmod.reviewer_entry(e) for e in cfg["reviews"][s["risk"]]["code"]]' \
  $T/test_merge.py::test_gate_workflow_between_senior_and_codex_go_is_refused
mutate "merge runs inside a worker" devloop/merge.py \
  '    if os.environ.get("DEVLOOP_RUN"):' '    if False:' \
  $T/test_merge.py::test_merge_refuses_inside_a_worker
mutate "guard denies any mention of a command" hooks/headless_merge_guard.py \
  '        elif exe == "devloop-merge" and worker:' '        elif "devloop-merge" in text and worker:' \
  $T/guard/test_guard_segments.py
mutate "memory reservations stay per project" devloop/runs.py \
  '    for name in projmod.project_names():' '    for name in [n for n in projmod.project_names() if n != "p"]:' \
  $T/test_runs_limits.py::test_two_projects_share_the_memory_headroom
mutate "the reservation amount is not frozen at dispatch" devloop/dispatch.py \
  '        req["memory_mb"] = projmod.memory_mb(cfg, kind)' '        pass' \
  $T/test_runs_limits.py::test_dispatch_freezes_the_reservation_amount

mutate "transcript cache keeps paths nobody needs" devloop/dash/transcript.py \
  '        for path in [p for p in self.entries if p not in keep]:
            del self.entries[path]' '        pass' \
  $T/dash/test_transcript.py::test_cache_evicts_a_previously_cached_path
mutate "reader cursor jumps to the fstat size" devloop/dash/transcript.py \
  '        entry.offset = start + len(data)' '        entry.offset = end' \
  $T/dash/test_transcript.py::test_truncate_between_fstat_and_read_then_regrow
mutate "one bad transcript line stops the reader" devloop/dash/transcript.py \
  '        except _LINE_ERRORS:
            return' '        except ZeroDivisionError:
            return' \
  $T/dash/test_transcript.py::test_parser_error_on_one_line_keeps_the_next
mutate "reader trusts the stat identity, not the open file" devloop/dash/transcript.py \
  '        ident, end = (fs.st_dev, fs.st_ino), fs.st_size' '        ident, end = (entry.ident or (fs.st_dev, fs.st_ino)), fs.st_size' \
  $T/dash/test_transcript.py::test_path_replaced_between_stat_and_open_reads_new_file_only
mutate "the first read ignores the tail limit" devloop/dash/transcript.py \
  '        if (first and end > TAIL_BYTES) or end - entry.offset > STEP_BYTES:' '        if end - entry.offset > STEP_BYTES:' \
  $T/dash/test_transcript.py::test_first_read_is_bounded_by_tail_bytes

mutate "a shell command word reaches the page without the allowlist" devloop/dash/transcript.py \
  '        return word if word in PUBLIC_COMMANDS else "?"' '        return word' \
  $T/test_dash_server.py::test_transcript_secrets_reach_no_route $T/dash/test_transcript.py::test_shell_word_cases
mutate "drawer run history is filed under the wrong key" devloop/dash/view.py \
  '                    self.history.setdefault(rec["key"], []).append(rec)' \
  '                    self.history.setdefault(rec["role"], []).append(rec)' \
  $T/dash/test_live.py::test_drawer_keeps_finished_run_history
mutate "the wrapper keeps its own list of Codex kinds" devloop/wrap.py \
  'CODEX_KINDS = tuple(k for k, s in runs.KINDS.items() if s.harness == "codex")' \
  'CODEX_KINDS = ("codex-review", "codex-fix")' \
  $T/dash/test_transcript.py::test_codex_kinds_derived_from_registry
mutate "an event kind carries a tag outside the frozen set" devloop/status.py \
  '{e.get('"'"'key'"'"')}", "DROPPED"' '{e.get('"'"'key'"'"')}", "DROP"' \
  $T/test_dash_view.py::test_every_event_kind_has_a_tag
mutate "the view schema number stays at 1" devloop/dash/view.py \
  'SCHEMA = 2' 'SCHEMA = 1' \
  $T/test_dash_view.py::test_schema_is_two
mutate "a live card key is renamed in the model only" devloop/dash/live.py \
  '                "tools": st.tools if st else 0,' '                "tool_count": st.tools if st else 0,' \
  $T/test_dash_view.py::test_view_keys_match_schema $T/dash/test_live.py::test_live_card_fields

mutate "a held slice shows twice in Needs you" devloop/dash/view.py \
  '    held_keys = {s["key"] for s in ctx.slices if _is_hold(s)}' \
  '    held_keys = {s["key"] for s in ctx.slices if s["phase"] == "HELD"}' \
  $T/test_dash_view.py::test_each_held_or_parked_slice_shows_once
mutate "the tick probe creates its lock file" devloop/tick.py \
  '        st = os.stat(store.state_path(project) / "tick.lock")' \
  '        (store.state_dir(project) / "tick.lock").touch()
        st = os.stat(store.state_path(project) / "tick.lock")' \
  $T/dash/test_controls.py::test_get_routes_write_nothing
mutate "an answer is marked done before its effects and event" devloop/notes.py \
  '        _effects(project, q["key"], receipt["choice"])
' \
  '        q["decision"] = {**receipt, "status": DONE}
        q["applied"] = receipt["choice"]
        _save_questions(path, before, qs, after, had)
        _effects(project, q["key"], receipt["choice"])
' \
  $T/dash/test_controls.py::test_answer_event_once_across_append_failure
mutate "a replayed answer appends its event again" devloop/notes.py \
  '            once=("decision", receipt["id"]),' '            once=None,' \
  $T/dash/test_controls.py::test_answer_event_once_across_append_failure
mutate "a POST is not checked for its token" devloop/dash/actions.py \
  '    if not hmac.compare_digest(token.encode(), handler.server.token.encode()):' \
  '    if False:' \
  $T/dash/test_controls.py::test_every_post_route_refuses_bad_token_or_origin
mutate "a POST is not checked for its origin" devloop/dash/actions.py \
  '    if not _origin_ok(handler):' '    if False:' \
  $T/dash/test_controls.py::test_every_post_route_refuses_bad_token_or_origin
mutate "the answer key is sealed as a plain id" devloop/dash/view.py \
  '            "answer_key": "qkey?",' '            "answer_key": "id?",' \
  $T/dash/test_controls.py::test_hold_form_key_survives_a_hex_looking_slice_name
mutate "a blank form field is dropped from a POST" devloop/dash/actions.py \
  '            keep_blank_values=True,' '            keep_blank_values=False,' \
  $T/dash/test_controls.py::test_post_body_contract
mutate "the version is compared before the questions lock" devloop/notes.py \
  '    return _apply_answer(project, key, choice, via=via, version=expect_version)' \
  '    pre = load_questions(questions_path(project))[1].get(key)
    if (
        expect_version is not None
        and pre is not None
        and expect_version != question_version(pre)
    ):
        raise AnswerStale(MSG_STALE)
    return _apply_answer(project, key, choice, via=via, version=None)' \
  $T/dash/test_controls.py::test_reask_between_check_and_lock_is_refused
mutate "the command line pauses without the shared path" devloop/state_cli.py \
  '            store.set_paused(args.project, args.cmd == "pause", "cli")' \
  '            flag = store.state_dir(args.project) / "PAUSE"
            flag.touch() if args.cmd == "pause" else flag.unlink(missing_ok=True)' \
  $T/dash/test_controls.py::test_cli_and_dashboard_pause_share_one_path
mutate "a second digest rule for question versions" devloop/notes.py \
  '    return store.canonical_digest(q)' \
  '    return __import__("hashlib").sha256(repr(sorted(q.items())).encode()).hexdigest()[:16]' \
  $T/dash/test_controls.py::test_version_digest_fixtures
mutate "a page without a Class line reads as product" devloop/notes.py \
  '            klass = "tech" if raw.get("wait") == "no" else "product"' \
  '            klass = "product"' \
  $T/dash/test_controls.py::test_old_questions_page_class_from_wait
mutate "the tick acknowledges decisions it did not carry" devloop/tick.py \
  '        state[snapshot.ACKED] = {**(state.get(snapshot.ACKED) or {}), **snap.decisions}' \
  '        state[snapshot.ACKED] = {**(state.get(snapshot.ACKED) or {}), **snapshot.build(project).decisions}' \
  $T/test_tick.py::test_override_during_model_call_reaches_next_prompt
mutate "an unacknowledged decision does not wake a fresh tick" devloop/tick.py \
  '        and not snap.decisions' '        and True' \
  $T/test_tick.py::test_override_during_model_call_reaches_next_prompt
mutate "overlapping tick posts both spawn" devloop/dash/actions.py \
  '    with srv.tick_lock:' '    with __import__("contextlib").nullcontext():' \
  $T/dash/test_controls.py::test_overlapping_tick_posts_spawn_once
G=$T/guard
mutate "the push SHA header keeps duplicate or spaced head lines" devloop/store.py \
  '{**header, **fields}.items()' 'header.items()' \
  $G/test_push_header.py
mutate "a result that cannot be published still exits clean" devloop/wrap.py \
  '        if code == 0:  # a result that could not be published is not a success' '        if False:' \
  $G/test_push_header.py::test_result_publication_failure_sets_a_failed_exit
mutate "apply forgets the writer record" devloop/apply.py \
  '        outcome = slices.apply_effects(project, key, run_id, writer=writer)' \
  '        outcome = slices.apply_effects(project, key, run_id)' \
  $G/test_writer_provenance.py::test_apply_run_binds_kind_commit_and_lineage
mutate "a moved head keeps the old writer" devloop/evidence.py \
  '        and rec["sha"] == s["head"]' '        and True' \
  $G/test_writer_provenance.py::test_head_moved_without_a_writer_run_fails_closed
mutate "a legacy slice does not recover its writer from runs" devloop/evidence.py \
  '    found = _applied_writer_runs(project, s)
    if found:' '    found = []
    if found:' \
  $G/test_writer_provenance.py::test_legacy_slice_recovers_the_writer_from_its_applied_run
mutate "an unknown writer shows no unmet gate item" devloop/evidence.py \
  '    if head_writer(project, s) is None:
        items.append(' '    if False:
        items.append(' \
  $G/test_writer_provenance.py::test_legacy_string_without_a_run_is_an_unmet_item
mutate "the Senior seat accepts any reviewer kind at evidence time" devloop/evidence.py \
  '        return ("dennis",)' '        return _reviewer_kinds(cfg, name)' \
  $G/test_writer_provenance.py::test_dual_kind_codex_go_does_not_satisfy_the_senior_seat
mutate "dispatch lets the Senior seat run a non-dennis kind" devloop/dispatch.py \
  '    if purpose == "code" and kind not in evidence.code_reviewer_kinds(' '    if False and kind not in evidence.code_reviewer_kinds(' \
  $G/test_writer_provenance.py::test_dual_kind_senior_cannot_run_codex_review_on_a_codex_head
mutate "workers run without the guard hooks" devloop/wrap.py \
  '        env = githooks.protect_env(env, rd / "githooks")' '        pass' \
  $G/test_worker_push.py::test_worker_push_to_main_is_refused_and_a_feature_push_works
mutate "the guard pre-push lets a base ref through" devloop/githooks.py \
  '    if name in bases and worker:' '    if False:' \
  $G/test_worker_push.py::test_worker_push_to_main_is_refused_and_a_feature_push_works
mutate "the guard hooks drop the repo's own hooks" devloop/githooks.py \
  '  exec "$orig" "$@"' '  exit 0' \
  $G/test_worker_push.py::test_repo_own_hooks_still_run_for_a_worker
mutate "the guard pre-push drops the repo's own pre-push" devloop/githooks.py \
  '  "$orig" "$@" < "$tmp"
  exit $?' '  exit 0' \
  $G/test_worker_push.py::test_repo_pre_push_failure_still_blocks_a_worker_push
mutate "a legacy reservation uses the wrong project's estimate" devloop/runs.py \
  '            total += projmod.memory_mb(owner_cfg, req.get("kind"))' '            total += 1' \
  $G/test_host_memory.py::test_legacy_request_uses_its_owning_projects_estimate
mutate "a damaged proc record hides a reservation" devloop/runs.py \
  '            req = store.read_json(rd / "request.json")
        except (store.StoreError, OSError):
            continue
        if req:
            yield rd, req' '            req = _merged(rd)
        except (store.StoreError, OSError):
            continue
        if req:
            yield rd, req' \
  $G/test_host_memory.py::test_damaged_process_record_in_another_project_keeps_its_reservation
mutate "a naive timestamp is compared as is" devloop/runs.py \
  '    if moment.tzinfo is None:' '    if False:' \
  $G/test_host_memory.py::test_naive_start_time_is_read_as_utc
mutate "a shell comment is read as syntax" hooks/headless_merge_guard.py \
  '        elif _is_comment_start(command, i):' '        elif False:' \
  $T/guard/test_guard_segments.py::test_a_worker_may_name_a_command_without_running_it
mutate "heredoc terminators ignore spaces" hooks/headless_merge_guard.py \
  '            if (line.lstrip("\t") if tabs else line) == end:' '            if line.strip() == end:' \
  $T/guard/test_guard_segments.py::test_a_worker_may_name_a_command_without_running_it
mutate "gh is matched by text, not position" hooks/headless_merge_guard.py \
  '        elif exe == "gh":
            if _gh_blocked(args, text):
                return True
        elif' '        elif exe == "gh" and _GH_PR_MERGE.search(text):
            return True
        elif' \
  $T/guard/test_guard_segments.py::test_a_worker_may_name_a_command_without_running_it
mutate "a wrong-typed closed mark still counts" devloop/status.py \
  '    if not _entries_ok(closures, lambda e: type(e.get("closed", False)) is bool):' '    if not _entries_ok(closures, lambda e: True):' \
  $T/dashboard/test_ledgers.py
mutate "a wrong ledger container still reaches the shipped cards" devloop/status.py \
  '    if not _entries_ok(merges, lambda e: True):' '    if False:' \
  $T/dashboard/test_ledgers.py
mutate "capacity pools ignore the registry order" devloop/dash/view.py \
  '        for pool in projmod.SLOTS
    ]' '        for pool in ("builder", "tester", "reviewer", "planner")
    ]' \
  $T/test_dash_view.py::test_displayed_pools_follow_the_registry_order

G=$T/guard
mutate "the hook forgets inherited Git configuration" devloop/githooks.py \
  'GIT_CONFIG_COUNT={count} git rev-parse' 'GIT_CONFIG_COUNT=0 git rev-parse' \
  $G/test_worker_push.py::test_inherited_hooks_path_can_reject_a_worker_commit
mutate "the hook resolves hooks from a fixed repository" devloop/githooks.py \
  'dir=$(GIT_CONFIG_COUNT={count} git rev-parse' 'dir=$(GIT_CONFIG_COUNT={count} git -C /nonexistent rev-parse' \
  $G/test_worker_push.py::test_hooks_of_the_repository_running_git_are_the_ones_that_run
mutate "post-index-change is not forwarded" devloop/githooks.py \
  '    "post-index-change",
' '' \
  $G/test_worker_push.py::test_hooks_of_the_repository_running_git_are_the_ones_that_run
mutate "pre-push ignores a failed input copy" devloop/githooks.py \
  'cat > "$tmp" || exit 1' 'cat > "$tmp"' \
  $G/test_worker_push.py::test_pre_push_rejects_when_it_cannot_copy_its_input
mutate "recovery trusts the raw DONE header" devloop/evidence.py \
  '        res = runs.parse_result(rd, req["kind"])' \
  '        res = runs.Result(store.read_header(store.out_dir(rd) / "result.md").get("status"), store.read_header(store.out_dir(rd) / "result.md").get("head"), None, None, None, None, "", "")' \
  $G/test_writer_provenance.py::test_a_failed_run_with_a_done_header_is_not_credited
mutate "an import records no commit" devloop/slices.py \
  '            overrides["head_writer"] = writer_record("imported", head)' '            pass' \
  $G/test_writer_provenance.py::test_the_imported_head_counts_as_claude_written
mutate "an imported head is trusted at any commit" devloop/evidence.py \
  '        and rec["sha"] == s["head"]' '        and True' \
  $G/test_writer_provenance.py::test_moving_an_imported_head_restores_the_unmet_writer_item
mutate "the Senior seat ignores the kinds a reviewer may run" devloop/evidence.py \
  ' and "dennis" in _reviewer_kinds(cfg, n)' '' \
  $G/test_writer_provenance.py::test_senior_seat_skips_a_dennis_kind_reviewer_that_cannot_run_dennis
mutate "the header ends only at an exact separator line" devloop/store.py \
  '        if line.strip() == "---":' '        if line == "---\n":' \
  $G/test_push_header.py::test_a_separator_with_trailing_spaces_keeps_the_body
mutate "a head that already equals the pushed one is left as written" devloop/wrap.py \
  '        store.rewrite_header(path, head=pushed)' \
  '        if hdr.get("head") != pushed:
            store.rewrite_header(path, head=pushed)' \
  $G/test_push_header.py::test_a_stale_head_before_the_correct_one_leaves_exactly_one
mutate "a request with bad UTF-8 escapes the memory scan" devloop/store.py \
  '    except UnicodeDecodeError:
        text, bad = "", True' '    except ZeroDivisionError:
        text, bad = "", True' \
  $G/test_host_memory.py::test_damaged_request_files_are_skipped_and_healthy_runs_still_count
mutate "frozen memory depends on a valid run kind" devloop/runs.py \
  'if type(frozen) is int and frozen >= 0:' 'if type(frozen) is int and frozen >= 0 and isinstance(req.get("kind"), str):' \
  $G/test_host_memory.py::test_frozen_memory_counts_with_a_damaged_kind
mutate "a wrong-typed kind reaches the estimate lookup" devloop/runs.py \
  '            if not isinstance(req.get("kind"), str):' '            if False:' \
  $G/test_host_memory.py::test_damaged_request_files_are_skipped_and_healthy_runs_still_count
mutate "a null ledger reads as a failed read" devloop/dash/view.py \
  '        if raw is not unread and ok is None:' '        if raw is not None and raw is not unread and ok is None:' \
  $T/dashboard/test_ledgers.py::test_a_null_ledger_is_marked_unreadable
mutate "a base side that touches a slice file still carries evidence" devloop/carry.py \
  '    clash = sorted(own & incoming)' '    clash = []' \
  $T/test_carry.py::test_main_touching_a_slice_file_does_not_carry
mutate "slices of one row are listed as overlapping" devloop/overlap.py \
  'if a["row"] == b["row"] or a["repo"] != b["repo"]:' 'if a["repo"] != b["repo"]:' \
  $T/test_overlap.py::test_same_row_is_not_listed

# PUSH-GATE S1: the ref policy in git pre-push, the PR gate, and the merge guard split.
R=$G/test_ref_policy.py
P=tests/test_pr_gate.py
GH=devloop/githooks.py
mutate "P1 lets a delete through" $GH \
  '    if _ZERO.fullmatch(loid):' '    if False:' $R
mutate "P2a lets a worker create a tag" $GH \
  'and _ZERO.fullmatch(roid) and not worker:' 'and _ZERO.fullmatch(roid):' $R
mutate "P4 treats a missing remote object as an ancestor" $GH \
  '    if _git(["cat-file", "-e", f"{roid}^{{commit}}"], cwd)[0] != 0:' '    if False:' $R
mutate "P6 always finds the remote tip in the reflog" $GH \
  '    if not _in_reflog(roid, lref, cwd):' '    if False:' $R
mutate "P3 ignores DEVLOOP_RUN" $GH \
  '    if name in bases and worker:' '    if False:' $R
mutate "P0 anchors with match instead of fullmatch" $GH \
  'isinstance(text, str) and _OID.fullmatch(text) is not None' 'isinstance(text, str) and _OID.match(text) is not None' $R
mutate "the override is honored for a worker" $GH \
  ' == "1" and not env.get("DEVLOOP_RUN"):' ' == "1":' $R
mutate "the PR gate drops the retarget refusal" hooks/pr_gate.py \
  '        if create and _RETARGET.search(command):' '        if False:' $P
mutate "the PR gate allows on an exception" hooks/pr_gate.py \
  '        return _deny("pr gate could not check this command; run devloop-install")' '        return 0' $P
mutate "the skip-hooks check is gone" hooks/pr_gate.py \
  '        if skip:
            return _deny(SKIP_REASON)' '        if False:
            return _deny(SKIP_REASON)' $P
mutate "the installer writes to the worktree git dir" $GH \
  '["rev-parse", "--path-format=absolute", "--git-common-dir"]' '["rev-parse", "--path-format=absolute", "--git-dir"]' $R
mutate "the receipt compare is a prefix match" hooks/pr_gate.py \
  'and receipt == head:' 'and receipt.startswith(head):' $P
mutate "an unborn HEAD matches a missing receipt" hooks/pr_gate.py \
  'githooks.is_object_id(head) and receipt == head' 'receipt == head' \
  "$P::test_an_unborn_head_without_a_receipt_denies_create"
mutate "every remote is protected" $GH \
  '    if len(argv) > 1 and not is_protected_url(argv[1], cwd):' '    if False:' $R
mutate "P2a allows a tag update" $GH \
  'and _ZERO.fullmatch(roid) and not worker:' 'and not worker:' $R
mutate "no remote is protected" $GH \
  'def is_protected_url(url, cwd=None) -> bool:
' 'def is_protected_url(url, cwd=None) -> bool:
    return False
' $R
mutate "the merge guard checks a loop worker's push text again" hooks/headless_merge_guard.py \
  '            check_base_pushes=bool(event.get("agent_id")) and not in_run,' '            check_base_pushes=worker,' \
  $G/test_merge_guard.py
mutate "the PR gate imports the package at the top" hooks/pr_gate.py \
  'import json
import os
import re
' 'import json
import os
import re
from devloop import project  # noqa: F401
' $P
mutate "the policy file imports the package at the top" $GH \
  'import os
import re
' 'import os
from devloop import store  # noqa: F401
import re
' $R
mutate "the settings command turns exit 2 into the install message" $P \
  '2) exit 2;; esac; fi; "' 'esac; fi; "' $P
mutate "the pre-push hook leaves its temp copy" $GH \
  "trap 'rm -f \"\$tmp\"' EXIT" ':' $R
mutate "a refusal prints the push URL" $GH \
  '        print(message, file=sys.stderr)' '        print(message, argv[1:2], file=sys.stderr)' $R
mutate "a bare env feeds no pipe check" hooks/headless_merge_guard.py \
  '            if sep == "|":
                continue
            raise' '            raise' \
  $G/test_merge_guard.py
mutate "the PR gate allows a create when a config is unreadable" hooks/pr_gate.py \
  '        if unreadable:' '        if create and False:' $P
mutate "the PR gate allows a skip word when a config is unreadable" hooks/pr_gate.py \
  '        if unreadable:' '        if create and unreadable:' $P
mutate "the PR gate reads the receipt under a fixed name" hooks/pr_gate.py \
  '(Path(git_dir) / receipt)' '(Path(git_dir) / "devloop-quality-gate")' $P
mutate "the receipt writer ignores the project's receipt name" bin/devloop-gate-pass \
  '        "receipt"
    ]' '        "receipt"
    ] and project.PUSH_GATE_RECEIPT' $P
mutate "an unlisted repo gets no push gate defaults" devloop/project.py \
  '    return push_gate({})

' '    return {}

' tests/devloop/test_push_gate_config.py
mutate "the receipt name may be any plain file name" devloop/project.py \
  'RECEIPT_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*-quality-gate")' \
  'RECEIPT_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")' tests/devloop/test_push_gate_config.py
mutate "the repo listing drops the project's gate names" devloop/project.py \
  'Path(repo["path"]).resolve(), push_gate(cfg))' 'Path(repo["path"]).resolve(), push_gate({}))' \
  tests/devloop/test_push_gate_config.py
mutate "the PR gate allows a create joined to another command" hooks/pr_gate.py \
  '        if create and _COMPOUND.search(_REDIRECT.sub("", command)):' '        if create and False:' $P
scope_check=$(sed -n '/^        gate, unreadable = _configured(cwd)$/,/^            return 0$/p' "$WORK/hooks/pr_gate.py")
standalone_check='        if create and _COMPOUND.search(_REDIRECT.sub("", command)):
            return _deny(STANDALONE_REASON)'
mutate "the PR gate checks for shell joiners before checking repo scope" hooks/pr_gate.py \
  "$scope_check
$standalone_check" "$standalone_check
$scope_check" "$P::test_a_heredoc_body_is_allowed_outside_guarded_repos"
mutate "the PR gate treats a newline as no separator" hooks/pr_gate.py \
  '_COMPOUND = re.compile(r"[;&|\r\n`]|\$\(|[<>]\(")' '_COMPOUND = re.compile(r"[;&|`]|\$\(|[<>]\(")' $P
mutate "the installer checks only the local hooks path" $GH \
  '["config", "--get", "core.hooksPath"]' '["config", "--local", "--get", "core.hooksPath"]' \
  tests/devloop/test_push_gate_config.py
mutate "the object id check accepts a short id" $GH \
  '    return isinstance(text, str) and _OID.fullmatch(text) is not None' \
  '    return isinstance(text, str) and re.fullmatch(r"[0-9a-f]{7,64}", text) is not None' \
  tests/devloop/test_push_gate_config.py $R
mutate "the shim ignores the project's override name" $GH \
  '{OVERRIDE_VAR}={override_env} exec' '{OVERRIDE_VAR}={DEFAULT_OVERRIDE_ENV} exec' tests/devloop/test_push_gate_config.py
mutate "the override accepts a variable the shim did not name" $GH \
  '    name = env.get(OVERRIDE_VAR)' '    name = env.get(OVERRIDE_VAR) or DEFAULT_OVERRIDE_ENV' tests/devloop/test_push_gate_config.py
mutate "the push gate block accepts any receipt name" devloop/project.py \
  'if not isinstance(value, str) or not pattern.fullmatch(value):' 'if not isinstance(value, str):' tests/devloop/test_push_gate_config.py
mutate "configured_repos drops an unreadable config silently" devloop/project.py \
  '            unreadable.append(name)' '            pass' \
  tests/devloop/test_configured_repos.py $P
mutate "the installer reads project configs by its own walk" devloop/install.py \
  '    repos, unreadable = project.configured_repos()' \
  '    repos, unreadable = [], []' tests/devloop/test_configured_repos.py

echo
echo "killed: $killed  survived: $survived  stale: $stale  red-base: $redbase"
[ "$survived" -eq 0 ] && [ "$stale" -eq 0 ] && [ "$redbase" -eq 0 ]
