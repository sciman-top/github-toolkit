# 2026-05-14 code health refactor

## Scope
- Rule IDs: R1, R2, R6, R8, E4
- Risk level: low local code refactor
- Target: `sync-forks.py`, `test_toolkit.py`
- Real GitHub write: no

## Commands
- `git status --short --branch`
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- `python -m unittest test_toolkit.py`
- `python -m ruff check .`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
- `git diff --check`

## Evidence
- Baseline before edits passed `py_compile`, `unittest`, and `benchmark_hotpaths --check`.
- Final `py_compile` passed.
- Final unit test passed: 163 tests.
- Final `ruff` passed.
- Final `mypy` passed.
- Final benchmark passed with no threshold violations.
- Final `git diff --check` reported only line-ending warnings for `sync-forks.py` and `test_toolkit.py`.
- Code changes:
  - Precomputed stable-source repo lookup set instead of rebuilding it on each policy check.
  - Centralized `source_update_policy` and common fork row fields used by release, schedule, and operation reports.
  - Centralized latest workflow-run parsing for schedule and smoke-run readiness checks.
  - Rejected mutually exclusive `sync-forks.py` modes explicitly instead of relying on implicit priority.
  - Added tests for mode conflicts, stable-source policy, and malformed workflow-run payload fallback.

## Rollback
- Revert the local commit or restore these paths from git history:
  - `sync-forks.py`
  - `test_toolkit.py`
  - `docs/change-evidence/2026-05-14-code-health-refactor.md`
