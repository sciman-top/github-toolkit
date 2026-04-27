# 2026-04-27 Review Optimization Evidence

## Scope
- Rule IDs: `R1`, `R2`, `R3`, `R6`, `R8`, `E4`, `E5`, `E6`
- Risk level: low to medium
- Current location: `D:\CODE\github-toolkit`
- Target home: core Python GitHub toolkit helpers and CLI boundary tests
- Real GitHub writes: no

## Changes
- Hardened `paginate()` so malformed page payloads and `MAX_PAGES` exhaustion are surfaced as `invalid_response` when callers request error details.
- Made fork discovery fail closed when per-fork metadata lookup fails instead of silently using `parent=unknown`.
- Added fork-discovery observability fields for parent metadata hydration: `parent_detail_lookup_count` and `parent_detail_cache_hit_count`.
- Added a per-run parent metadata cache and fail-closed handling for successful detail responses that still lack parent metadata.
- Classified HTTP `429` as `rate_limit` even when the `gh` stderr text does not include a rate-limit phrase.
- Added URL encoding for branch/ref values in tree lookup, workflow contents lookup, and add-workflow verification paths.
- Normalized empty parent metadata fields back to safe defaults.
- Added `fork_discovery_hydration` to `scripts/benchmark_hotpaths.py` so the discovery hydration path is covered by the synthetic benchmark.
- Added regression coverage for the above contracts.

## Verification
- `python --version`
  - `Python 3.13.7`
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, `Ran 131 tests ... OK`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 4 source files`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`; includes `fork_discovery_hydration` with mean `0.154ms` at size 10, `1.298ms` at size 100, and `6.507ms` at size 500
- `python list-forks.py --json`
  - passed, `repo_count=6`, `fork_count=2`, `parent_detail_lookup_count=2`, both forks `workflow_installed=true`, `behind_by=0`
- `python sync-forks.py --json --dry-run`
  - passed, `matched=2`, `success=2`, `parent_detail_lookup_count=2`, real writes disabled
- `python sync-forks.py --json --add-workflow --dry-run`
  - passed, `parent_detail_lookup_count=2`, both repos reported `SKIP: sync-fork.yml already matches current workflow template`
- `codex --version`
  - passed, `codex-cli 0.125.0`
- `codex --help`
  - passed

## Rollback
- Local code rollback: use git history for `gh_utils.py`, `list-forks.py`, `sync-forks.py`, `test_toolkit.py`, `scripts/benchmark_hotpaths.py`, and this evidence file.
- Remote rollback: not required; no real GitHub write operation was executed in this task.

## Notes
- Existing pre-task working tree changes in `.governed-ai/repo-profile.json`, `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` were left untouched.
- `git diff` reported expected local LF-to-CRLF warnings; no functional gate failed.

## Follow-up Review Optimization at 2026-04-27T15:38Z

### Scope
- Rule IDs: `R1`, `R2`, `R3`, `R6`, `R8`, `E4`, `E5`
- Risk level: low
- Current location: `D:\CODE\github-toolkit`
- Target home: GitHub CLI boundary handling, Claude Code hook guard, CI/project gate coverage
- Real GitHub writes: no

### Changes
- Hardened `gh_utils.detect_auth_mode()` and `gh_utils.gh_api()` so `OSError` from launching `gh` becomes structured environment/auth context output instead of an uncaught exception.
- Hardened `.claude/hooks/governed-pre-tool-use.py` so bare `powershell` and `powershell.exe` commands are blocked, while `pwsh` remains allowed.
- Added regression tests for `gh` launch failure classification, hook direct-PowerShell matching, and sensitive path detection.
- Added `.claude/hooks/governed-pre-tool-use.py` to CI/local compile gates and synchronized the project-level `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` gate text.

### Verification
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, `Ran 137 tests ... OK`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 4 source files`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`; size 500 means: `collect_fork_statuses=34.158ms`, `fork_discovery_hydration=17.271ms`, `sync_all_report=19.081ms`, `sync_specific_deduped=36.764ms`
- Hook smoke checks
  - `powershell` -> blocked with hook exit `2`
  - `powershell.exe` -> blocked with hook exit `2`
  - `pwsh -NoProfile -Command Get-Location` -> allowed with hook exit `0`
  - `Read C:/tmp/.env` -> blocked with hook exit `2`
- `python list-forks.py --json`
  - passed, `repo_count=6`, `fork_count=2`, both forks `workflow_installed=true`, `behind_by=0`
- `python sync-forks.py --json --dry-run`
  - passed, `matched=2`, `success=2`, real writes disabled
- `python sync-forks.py --json --add-workflow --dry-run`
  - passed, both repos reported `SKIP: sync-fork.yml already matches current workflow template`
- `codex --version`; `codex --help`; `python --version`; `gh --version`; `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1 -SkipGh -Json`
  - passed, Codex `0.125.0`, Python `3.13.7`, gh `2.89.0`, environment probe `ok=true`
- `git diff --check`
  - passed; only expected LF-to-CRLF working-copy warnings were emitted

### Rollback
- Local rollback: use git history or revert this follow-up diff for `.claude/hooks/governed-pre-tool-use.py`, `gh_utils.py`, `test_toolkit.py`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and this evidence file.
- Remote rollback: not required; no real GitHub write operation was executed.
