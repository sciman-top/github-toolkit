# 2026-05-04 Force Confirmation Hardening

## Scope
- Rule IDs: `R1`, `R2`, `R4`, `R6`, `R8`, `E4`, `E6`
- Risk level: low
- Current location: `D:\CODE\github-toolkit`
- Target home: `sync-forks.py` CLI safety boundary and documentation
- Real GitHub writes: no

## Review Finding
- Baseline review found that `--force` confirmation covered real sync mode only.
- Evidence command:
  `python -c "import importlib.util; spec=importlib.util.spec_from_file_location('sync_forks','sync-forks.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m._force_sync_confirmation_missing(True, False, True, False))"`
- Previous result: `False`, meaning `--add-workflow --force` could proceed without `SYNC_FORKS_FORCE_ACK=I_UNDERSTAND`.
- Expected project boundary: real `--force` writes require explicit confirmation; `--dry-run` and read-only `--verify-schedule` remain exempt.

## Changes
- Renamed force-confirmation helpers to match the broader real-write safety contract.
- Required explicit confirmation for every real `--force` write, including `--add-workflow --force`.
- Preserved `--dry-run --force` and `--verify-schedule --force` as non-mutating paths that do not require confirmation.
- Updated CLI help and README wording from sync-only to real-write confirmation.
- Added regression tests for unconfirmed and confirmed `--add-workflow --force`.
- Made the existing force-sync test isolate `SYNC_FORKS_FORCE_ACK` so it cannot pass accidentally from the caller environment.

## Verification
- `python -m py_compile sync-forks.py test_toolkit.py`
  - passed
- `python -m unittest test_toolkit.TestSyncForksMain`
  - passed, `Ran 17 tests ... OK`
- `python -c "import importlib.util; spec=importlib.util.spec_from_file_location('sync_forks','sync-forks.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m._force_confirmation_missing(True, False, False))"`
  - passed, result `True`
- `python -m unittest test_toolkit.py`
  - passed, `Ran 151 tests ... OK`
- `python -m unittest test_toolkit.py`
  - passed as contract/invariant, `Ran 151 tests ... OK`
- `python scripts\benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`
  - final 500-row means: `collect_fork_statuses=12.503ms`, `fork_discovery_hydration=6.541ms`, `sync_all_report=7.87ms`, `sync_specific_deduped=7.753ms`, `verify_schedule_readiness=46.082ms`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 4 source files`
- `python sync-forks.py --json --verify-schedule`
  - passed read-only, `ready=3/3`, `workflow_needs_update=0`, `missing_workflow=0`, `missing_secret=0`, `scheduled_run_missing_or_unhealthy=0`, `errors=0`
  - auth mode: `gh_login`; no token value was printed
- `git diff --check`
  - passed; Git reported only CRLF conversion warnings for touched tracked text files

## Rollback
- Local rollback: revert this task's diff for `sync-forks.py`, `test_toolkit.py`, `README.md`, and this evidence file.
- Remote rollback: not required; this task performed no real GitHub write operation.

## Follow-up Candidates
- Keep live read-only schedule verification as the closeout check when workflow contract semantics change.
- If `--force` gets new modes later, require each mode to explicitly classify whether it is mutating before execution.
