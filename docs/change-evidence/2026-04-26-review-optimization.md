# 2026-04-26 Review Optimization Evidence

## Scope
- Current landing: `D:\CODE\github-toolkit`
- Target home: repository code, tests, benchmark helper, and this evidence file
- Risk level: low
- Real GitHub writes: no

## Rules
- R1/R2/R8: scoped change, small-step verification, evidence recorded here
- R6: verified in order `build -> test -> contract/invariant -> hotspot`
- E4: hotspot reviewed with synthetic benchmark and live read-only/dry-run CLI paths
- E5: no new dependencies
- E6: no persisted data format or workflow YAML schema change

## Commands And Evidence
| Step | Command | Result | Key output |
| --- | --- | --- | --- |
| build baseline | `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py` | pass | exit 0 |
| test baseline | `python -m unittest test_toolkit.py` | pass | `Ran 108 tests ... OK` |
| hotspot baseline | `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check` | pass | no threshold violations |
| platform diagnostics | `codex --version`; `codex --help`; `python --version`; `gh --version`; `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1 -SkipGh -Json` | pass | Codex 0.125.0, Python 3.13.7, gh 2.89.0, environment probe `ok=true` |
| lint baseline | `python -m ruff check .` | fail before fix | unused `get_fork_repos` import in `sync-forks.py` |
| type baseline | `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py` | fail before fix | incompatible `patch` fallback type in `scripts/benchmark_hotpaths.py` |
| build final | `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py` | pass | exit 0 |
| test final | `python -m unittest test_toolkit.py` | pass | `Ran 114 tests ... OK` |
| lint final | `python -m ruff check .` | pass | `All checks passed!` |
| type final | `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py` | pass | `Success: no issues found in 4 source files` |
| hotspot final | `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check` | pass | no threshold violations |
| live read-only | `python list-forks.py --json` | pass | user `sciman-top`, fork_count 2, no writes |
| live dry-run sync | `python sync-forks.py --json --dry-run` | pass | 2/2 repos dry-run ok, no writes |
| live dry-run workflow | `python sync-forks.py --json --add-workflow --dry-run` | pass | 2/2 repos dry-run ok, remote workflow drift observed, no writes |
| follow-up build | `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py` | pass | exit 0 |
| follow-up test | `python -m unittest test_toolkit.py` | pass | `Ran 118 tests ... OK` |
| follow-up lint | `python -m ruff check .` | pass | `All checks passed!` |
| follow-up type | `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py` | pass | `Success: no issues found in 4 source files` |
| follow-up hotspot | `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check` | pass | no threshold violations |
| remote workflow preflight | `python sync-forks.py --json --add-workflow --dry-run` | pass | 2/2 repos would update |
| remote workflow apply | `python sync-forks.py --json --add-workflow` | pass | updated `sciman-top/claude-dashboard` and `sciman-top/v2ray-agent` |
| remote workflow recheck | `python sync-forks.py --json --add-workflow --dry-run` | pass | 2/2 repos report `SKIP: sync-fork.yml already matches current workflow template` |

## Changes
- Removed an unused import in `sync-forks.py`.
- Made `workflow_dispatch` detection scope-aware so job step text cannot masquerade as an event trigger.
- Treated boolean deletion counts as malformed compare payloads, preserving fail-closed safety behavior.
- Treated blank `GH_TOKEN` / `GITHUB_TOKEN` values as unconfigured instead of valid environment-token mode.
- Sent empty JSON payloads through `gh_api(..., payload={})` instead of dropping `--input -`.
- Reworked benchmark mock fallback loading so normal `unittest.mock` and constrained Windows fallback remain compatible with mypy.
- Added regression tests around these boundaries.
- Added branch/ref URL encoding for GitHub compare endpoints in Python CLI paths and generated fork-sync workflow.
- Added CI `ruff` and `mypy` verification with pinned tool versions.
- Applied the updated fork-sync workflow to `sciman-top/claude-dashboard` and `sciman-top/v2ray-agent` after dry-run preflight.

## Rollback
- Use git history or revert this evidence file plus the related changes in:
  - `.github/workflows/ci.yml`
  - `gh_utils.py`
  - `list-forks.py`
  - `sync-forks.py`
  - `scripts/benchmark_hotpaths.py`
  - `test_toolkit.py`
- Remote workflow rollback: restore the previous `.github/workflows/sync-fork.yml` in each affected fork from GitHub file history, or re-run this repository's previous committed `sync-forks.py --add-workflow` version if that exact previous template is required.

## Notes
- Initial `sync-forks.py --json --add-workflow --dry-run` reported workflow template drift for `sciman-top/claude-dashboard` and `sciman-top/v2ray-agent`; follow-up authorization applied the update and recheck confirmed both now match the current template.
