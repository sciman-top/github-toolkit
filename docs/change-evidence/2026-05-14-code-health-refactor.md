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
- `python list-forks.py --json`
- `python sync-forks.py --json --verify-schedule`
- `python sync-forks.py --json --dry-run`
- `python sync-forks.py --json --verify-releases`
- `git diff --check`

## Evidence
- Baseline before edits passed `py_compile`, `unittest`, and `benchmark_hotpaths --check`.
- Final `py_compile` passed.
- Final unit test passed: 168 tests.
- Final `ruff` passed.
- Final `mypy` passed.
- Final benchmark passed with no threshold violations.
- Live read-only fork discovery passed: 3 forks found for `sciman-top`.
- Live schedule readiness passed: `ready 3/3`, `stable_source_only=1`, no workflow drift, no missing secret.
- Live dry-run sync passed: 3/3 repo operations returned `ok`; no remote writes were performed.
- Live release retention returned the expected safety exit (`LASTEXITCODE=3`): `ready 2/3`.
  `sciman-top/cockpit-tools` now needs latest stable release mirror refresh from upstream `v0.23.4`; this was not performed because it is a medium remote GitHub write outside this low-risk local refactor.
- Final `git diff --check` reported only line-ending warnings for `docs/change-evidence/2026-05-14-code-health-refactor.md`, `sync-forks.py`, and `test_toolkit.py`.
- Code changes:
  - Precomputed stable-source repo lookup set instead of rebuilding it on each policy check.
  - Centralized `source_update_policy` and common fork row fields used by release, schedule, and operation reports.
  - Centralized latest workflow-run parsing for schedule and smoke-run readiness checks.
  - Rejected mutually exclusive `sync-forks.py` modes explicitly instead of relying on implicit priority.
  - Centralized mode option metadata so conflict validation and help-facing option names share one source.
  - Centralized verification summary exit-code mapping for schedule and release checks.
  - Centralized per-repo operation result counter updates.
  - Centralized release-retention and schedule-readiness `next_action` decisions in pure helpers.
  - Added tests for mode conflicts, mode flag selection, stable-source policy, malformed workflow-run payload fallback, verification exit-code mapping, operation summary counters, release-retention action mapping, and schedule action mapping.

## Rollback
- Revert the local commit or restore these paths from git history:
  - `sync-forks.py`
  - `test_toolkit.py`
  - `docs/change-evidence/2026-05-14-code-health-refactor.md`
