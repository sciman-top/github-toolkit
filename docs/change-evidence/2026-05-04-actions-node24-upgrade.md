# 2026-05-04 Actions Node 24 Upgrade

## Scope
- Rule IDs: `R1`, `R2`, `R6`, `R8`, `E4`, `E5`
- Risk level: low
- Current location: `D:\CODE\github-toolkit`
- Target home: GitHub Actions workflow supply-chain/runtime compatibility
- Real GitHub writes: no workflow-dispatch writes; push-triggered CI verification only

## Review Finding
- Push CI run `25325017066` passed but emitted a GitHub Actions warning that Node.js 20 actions are deprecated.
- The warning named `actions/checkout@v4` and `actions/setup-python@v5` and said GitHub will force Node.js 24 by default on 2026-06-02 and remove Node.js 20 from runners on 2026-09-16.
- Existing workflow references:
  - `.github/workflows/auto-setup-forks.yml`: `actions/checkout@v4`, `actions/setup-python@v5`
  - `.github/workflows/ci.yml`: `actions/checkout@v4`, `actions/setup-python@v5`
  - `.github/workflows/integration-api-smoke.yml`: `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`

## Release Evidence
- `gh api repos/actions/checkout/releases/latest --jq '{tag_name,html_url,name,published_at}'`
  - `v6.0.2`, published `2026-01-09T19:53:28Z`
- `gh api repos/actions/setup-python/releases/latest --jq '{tag_name,html_url,name,published_at}'`
  - `v6.2.0`, published `2026-01-22T03:04:50Z`
- `gh api repos/actions/upload-artifact/releases/latest --jq '{tag_name,html_url,name,published_at}'`
  - `v7.0.1`, published `2026-04-10T17:31:14Z`

## Changes
- Updated `actions/checkout@v4` to `actions/checkout@v6`.
- Updated `actions/setup-python@v5` to `actions/setup-python@v6`.
- Updated `actions/upload-artifact@v4` to `actions/upload-artifact@v7`.
- Touched only workflow dependency versions; Python CLI behavior and generated fork-sync workflow template were unchanged.

## Verification
- `rg -n "uses: actions/(checkout|setup-python|upload-artifact)@" .github\workflows`
  - all tracked workflow references now use `checkout@v6`, `setup-python@v6`, or `upload-artifact@v7`
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
  - passed
- `python -m unittest test_toolkit.py`
  - passed, `Ran 153 tests ... OK`
- `python -m unittest test_toolkit.py`
  - passed as contract/invariant, `Ran 153 tests ... OK`
- `python -m ruff check .`
  - passed, `All checks passed!`
- `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`
  - passed, `Success: no issues found in 4 source files`
- `python scripts\benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
  - passed, `threshold_check.violations=[]`
  - final 500-row means: `collect_fork_statuses=10.557ms`, `fork_discovery_hydration=6.08ms`, `sync_all_report=5.531ms`, `sync_specific_deduped=5.037ms`, `verify_schedule_readiness=29.975ms`

## Rollback
- Local rollback: revert this task's diff for `.github/workflows/*.yml` and this evidence file.
- Remote rollback: push the revert commit if the upgraded action majors fail on GitHub-hosted runners.
