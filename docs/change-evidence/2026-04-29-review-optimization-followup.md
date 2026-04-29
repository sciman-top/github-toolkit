# 2026-04-29 Review Optimization Follow-up

## Scope
- Rule IDs: `R1`, `R2`, `R3`, `R6`, `R8`, `E4`, `E5`
- Risk level: low
- Current location: `D:\CODE\github-toolkit`
- Target home: Python CLI boundary hardening, structured JSON stability, verify-schedule fail-closed handling
- Real GitHub writes: no

## Changes
- Hardened `sync-forks.py` `safety_check()` so removed-file entries with malformed `filename` values fail closed instead of reaching a runtime exception.
- Hardened `sync-forks.py` verify-schedule parsing so malformed `workflow_runs[0]` payloads degrade to `last_scheduled_run = null` and `wait_or_trigger_smoke_run` instead of crashing.
- Hardened `list-forks.py` so `behind_by` is only populated from a non-negative integer `ahead_by` value; malformed values now normalize to `null`.
- Added regression coverage for malformed removed filenames, malformed compare counts, malformed compare status values, malformed verify-schedule run items, and malformed `ahead_by` values.

## Verification
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, `Ran 144 tests ... OK`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py test_support.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 5 source files`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`
- `python list-forks.py --json`
  - passed, `repo_count=7`, `fork_count=3`, all returned repos `workflow_installed=true`, all `behind_by=0`
- `python sync-forks.py --json --dry-run`
  - passed, `matched=3`, `success=3`, `failed=0`, real writes disabled
- `git diff --check`
  - passed

## Rollback
- Local rollback: revert this task's diff for `list-forks.py`, `sync-forks.py`, `test_toolkit.py`, and this evidence file.
- Remote rollback: not required; no real GitHub write operation was executed in this task.

## Notes
- This slice intentionally avoided behavior changes to CLI flags, JSON top-level shape, sync force confirmation, and restore-point semantics.
