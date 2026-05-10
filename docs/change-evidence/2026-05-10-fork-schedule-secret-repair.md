# 2026-05-10 fork schedule secret repair

## Scope
- Rule IDs: R1, R2, R6, R8, E4, E5
- Risk: medium remote repository secret/write repair, low local code contract update
- Target home: fork sync schedule readiness and live GitHub Actions repair
- Real GitHub writes: yes
  - Set `SYNC_PAT` repository secret on `sciman-top/cockpit-tools`
  - Triggered `sync-fork.yml` on `sciman-top/cockpit-tools` via `workflow_dispatch`

## Basis
- Live readiness showed both current forks had `sync-fork.yml`, but only `sciman-top/v2ray-agent` was ready.
- `sciman-top/cockpit-tools` had `secret_sync_pat_present=false`, no prior schedule run, and `next_action=set_sync_pat_secret`.
- `gh auth status` showed active account `sciman-top` with `repo` and `workflow` scopes.

## Changes
- Wrote `SYNC_PAT` to `sciman-top/cockpit-tools` using the current `gh auth token`; token value was not printed.
- Triggered and watched `sciman-top/cockpit-tools` `sync-fork.yml` run `25627819373`; the workflow completed successfully.
- Updated `sync-forks.py --verify-schedule` so a successful `workflow_dispatch` smoke run can close readiness when a fork has no schedule history yet.
- Added regression coverage for successful smoke-run readiness.
- Documented `last_smoke_run` in `README.md`.

## Verification
- `gh secret list --repo sciman-top/cockpit-tools --json name,updatedAt`
  - `SYNC_PAT` present, `updatedAt=2026-05-10T11:42:32Z`
- `gh run watch 25627819373 --repo sciman-top/cockpit-tools --exit-status`
  - passed, including checkout, upstream resolve, safety check, restore point, diff guard, and merge-upstream
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, 154 tests
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, no threshold violations
- `python sync-forks.py --json --verify-schedule`
  - passed, `ready=2/2`, `missing_workflow=0`, `workflow_needs_update=0`, `missing_secret=0`, `scheduled_run_missing_or_unhealthy=0`, `errors=0`

## Rollback
- Local code rollback: revert this task's diff for `sync-forks.py`, `test_toolkit.py`, `README.md`, and this evidence file.
- Remote rollback if required: delete the `SYNC_PAT` secret from `sciman-top/cockpit-tools`; this would intentionally make that fork not ready again.
