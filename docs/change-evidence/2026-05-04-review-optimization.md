# 2026-05-04 Review Optimization

## Scope
- Rule IDs: `R1`, `R2`, `R6`, `R8`, `E4`, `E5`, `E6`
- Risk level: low
- Current location: `D:\CODE\github-toolkit`
- Target home: Python CLI robustness, schedule-readiness contract, shared parsing helper
- Real GitHub writes: no
- Pre-existing unrelated dirty files: governance rule/profile files were already modified before this task and were not edited by this slice.

## Review And Plan
- Baseline-first review covered correctness, security, performance, readability, architecture, CLI contracts, GitHub API boundaries, CI workflows, and hotspot benchmark behavior.
- The initial build, unit, lint, type, and benchmark baselines were already green.
- The actionable low-risk slice was to harden `--verify-schedule` so a stale existing workflow is not reported as ready, and to remove duplicated malformed integer parsing.

## Changes
- Added `gh_utils.parse_nonnegative_int()` and reused it from `list-forks.py` and `sync-forks.py`.
- Removed duplicate private non-negative integer parsers from `list-forks.py` and `sync-forks.py`.
- Hardened `sync-forks.py --verify-schedule`:
  - Adds `workflow_current` and `workflow_update_reason` per repo.
  - Adds `summary.workflow_needs_update`.
  - Treats existing but stale workflow content as `next_action=update_sync_workflow`.
  - Keeps missing workflow as `next_action=install_sync_workflow`.
- Added regression tests for the shared parser and stale workflow readiness handling.
- Documented `--verify-schedule` and its readiness fields in `README.md`.
- Refactored `sync-forks.py._run()` from a monolithic mixed-mode function into focused helpers for mode naming, report construction, fork resolution, verify-schedule handling, and sync/add-workflow operation reporting.
- Fixed verify-schedule mode exit semantics so `summary.workflow_needs_update > 0` returns `EXIT_SAFETY` instead of reporting success.
- Added regression coverage for `workflow_needs_update` mode-level safety exit behavior.
- Added synthetic benchmark coverage for `verify_schedule_readiness` and documented the benchmark scope in `README.md`.

## Verification
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, `Ran 147 tests ... OK`
- `python -m unittest test_toolkit.py`
  - passed as contract/invariant, `Ran 147 tests ... OK`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`
  - final 500-row mean timings after benchmark expansion: `collect_fork_statuses=13.111ms`, `fork_discovery_hydration=7.444ms`, `sync_all_report=7.691ms`, `sync_specific_deduped=8.927ms`, `verify_schedule_readiness=41.397ms`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 4 source files`
- `python sync-forks.py --json --verify-schedule`
  - passed read-only, `ready=3/3`, `workflow_needs_update=0`, `missing_workflow=0`, `missing_secret=0`, `scheduled_run_missing_or_unhealthy=0`, `errors=0`
  - auth mode: `gh_login`; no token value was printed
- `git diff --check`
  - passed; Git reported only CRLF conversion warnings for touched Python files
- Refactor shape check:
  - `_run()` reduced from 186 lines to 48 lines
  - extracted helpers: `_resolve_run_forks=23 lines`, `_run_verify_schedule_mode=21 lines`, `_run_mutating_or_dryrun_mode=32 lines`

## Rollback
- Local rollback: revert this task's diff for `gh_utils.py`, `list-forks.py`, `sync-forks.py`, `test_toolkit.py`, `README.md`, and this evidence file.
- Remote rollback: not required; this task performed no real GitHub write operation.

## Follow-up Candidates
- Consider splitting `sync-forks.py._run()` into smaller mode-specific functions in a separate refactor-only slice.
- Consider adding a benchmark for `--verify-schedule` row construction if schedule-readiness volume grows beyond the current fork count.
- Keep live schedule readiness verification as a read-only closeout check when modifying workflow contract semantics.
