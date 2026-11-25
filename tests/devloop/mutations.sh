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
  'if _ANYWHERE.search(text) or _GH_PR_MERGE.search(text):' 'if _ANYWHERE.search(text):' \
  $T/test_merge.py::test_merge_guard_denies_in_headless_mode
mutate "merge guard blocks every git push" hooks/headless_merge_guard.py \
  '    return any(
        _push_targets_base(' '    return bool(_GIT_PUSH.search(text)) or any(
        _push_targets_base(' \
  $T/test_merge.py::test_merge_guard_allows_reads_feature_pushes_and_devloop_merge
mutate "merge guard blocks any text with merge" hooks/headless_merge_guard.py \
  '    return any(
        _push_targets_base(' '    return "merge" in text or any(
        _push_targets_base(' \
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
  '        base = f"builder:{req.get('"'"'lineage'"'"')}"' '        base = f"builder:{req.get('"'"'head'"'"')}"' \
  $T/test_runs_integrity.py::test_builder_failures_count_across_new_heads
mutate "markdown is not stripped from the verdict" devloop/wrap.py \
  '    m = rx.fullmatch(_plain_line(lines[-1])) if lines else None' '    m = rx.fullmatch(lines[-1]) if lines else None' \
  $T/test_wrap.py::test_markdown_around_the_verdict_line_is_stripped
mutate "a closed PR is read as unknown" devloop/merge.py \
  '    return {"OPEN": "open", "CLOSED": "closed"}.get(state, "unknown")' '    return {"OPEN": "open"}.get(state, "unknown")' \
  $T/test_merge.py::test_closed_pr_clears_the_flags_and_refuses_with_a_fixed_reason
mutate "merge move re-checks the gate" devloop/slices.py \
  '            if via_merge
            else [name' '            if False
            else [name' \
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
mutate "board call repeats on replay" devloop/board.py \
  '        if rec is None or rec["board_done"]:' '        if rec is None:' \
  $T/test_merge.py::test_crash_after_the_move_still_counts_and_delivers_the_board_once
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
  '"outputs": [(n, DIGITS.sub("", t)) for n, t in outputs],' '"outputs": [(n, t) for n, t in outputs],' \
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

echo
echo "killed: $killed  survived: $survived  stale: $stale"
[ "$survived" -eq 0 ] && [ "$stale" -eq 0 ]
