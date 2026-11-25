#!/usr/bin/env bash
# Apply each mutation to a temp copy of the repo, run the tests that guard it, and expect
# a failure. A mutation whose tests still pass is a survivor. Usage: tests/devloop/mutations.sh
# The edits are exact-text replacements (a stale edit fails the script, so none can rot).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -r "$ROOT/devloop" "$ROOT/hooks" "$ROOT/bin" "$ROOT/skills" "$ROOT/tests" "$ROOT/conftest.py" "$ROOT/pyproject.toml" "$WORK/"
find "$WORK" -name __pycache__ -prune -exec rm -rf {} +
killed=0; survived=0; stale=0
declare -a SURVIVORS

# mutate NAME FILE OLD NEW TESTS...
mutate() {
  local name="$1" file="$2" old="$3" new="$4"; shift 4
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
  if (cd "$WORK" && "$PY" -m pytest -q -x -p no:cacheprovider "$@" >/dev/null 2>&1); then
    echo "SURVIVED $name"; survived=$((survived+1)); SURVIVORS+=("$name")
  else
    echo "killed   $name"; killed=$((killed+1))
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
  '    "test": Kind("tester", 7200, 1200, "testexit", False, True),' '' \
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
  '            _ANYWHERE.search(text) or _GH_PR_MERGE.search(text)' '            _ANYWHERE.search(text)' \
  $T/test_merge.py::test_merge_guard_denies_in_headless_mode
mutate "merge guard ignores a subagent id" hooks/headless_merge_guard.py \
  'worker = in_run or bool(event.get("agent_id"))' 'worker = in_run' \
  $T/test_merge.py::test_subagent_is_not_the_orchestrator
mutate "merge guard blocks every git push" hooks/headless_merge_guard.py \
  '            if _push_targets_base(" ".join(rest), root):' '            if True:' \
  $T/test_merge.py::test_merge_guard_allows_reads_feature_pushes_and_devloop_merge
mutate "merge guard blocks any text with merge" hooks/headless_merge_guard.py \
  '        elif exe == "git" and check_base_pushes:' '        elif "merge" in text:
            return True
        elif exe == "git" and check_base_pushes:' \
  $T/test_merge.py::test_merge_guard_allows_reads_feature_pushes_and_devloop_merge
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
  '        (_HEX, "[ref]"),' '' \
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
  '    if s["head_writer"] != "codex-builder":
        return roster' '    if True:
        return roster' \
  $T/test_runs_review_roster.py::test_r1_slice_fixed_by_codex_builder_needs_dennis_then_codex
mutate "tick starts workers with the user MCP servers" devloop/project.py \
  '    return ["--strict-mcp-config", "--mcp-config", path or NO_MCP]' '    return []' \
  $T/test_runs_limits.py::test_claude_workers_start_with_no_mcp_servers
mutate "memory admission ignores recent reservations" devloop/dispatch.py \
  '    held = runs.reserved_mb(cfg) + projmod.memory_mb(cfg, kind)' '    held = projmod.memory_mb(cfg, kind)' \
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
  '        if hdr.get("head") != pushed:' '        if False:' \
  $T/test_push.py::test_amended_head_is_pushed_and_reported
mutate "writer provenance is never recorded" devloop/slices.py \
  '                s["head_writer"] = writer' '                pass' \
  $T/test_runs_review_roster.py::test_apply_records_the_writer_of_the_head
mutate "a missing Senior reviewer no longer fails the gate" devloop/evidence.py \
  '    if senior_missing(cfg, s):' '    if False:' \
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
  $T/test_merge_guard_segments.py
mutate "memory reservations stay per project" devloop/runs.py \
  '    for name in _project_names():' '    for name in [n for n in _project_names() if n != "p"]:' \
  $T/test_runs_limits.py::test_two_projects_share_the_memory_headroom
mutate "the reservation amount is not frozen at dispatch" devloop/dispatch.py \
  '        req["memory_mb"] = projmod.memory_mb(cfg, kind)' '        pass' \
  $T/test_runs_limits.py::test_dispatch_freezes_the_reservation_amount

echo
echo "killed: $killed  survived: $survived  stale: $stale"
[ "$survived" -eq 0 ] && [ "$stale" -eq 0 ]
