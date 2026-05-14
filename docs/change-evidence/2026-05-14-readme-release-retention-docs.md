# 2026-05-14 README release retention docs

## Scope
- Rule IDs: R1, R2, R6, R8, E4
- Risk level: low documentation-only change
- Target: `README.md`, `docs/change-evidence/2026-05-14-fork-schedule-and-cockpit-backup.md`
- Real GitHub write: no

## Commands
- `git status --short --branch`
- `python sync-forks.py --help`
- `python list-forks.py --help`
- `rg -n "SYNC_PAT|stable_release_only|verify-releases|latest_stable" README.md sync-forks.py test_toolkit.py docs`
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- `python -m unittest test_toolkit.py`
- `git diff --check`

## Evidence
- README already documented the main fork sync workflow, schedule verification, stable-source policy, release retention policy, auth model, integration smoke workflow, and benchmark command.
- Documentation gap found: README did not explicitly state that `sync-forks.py --verify-releases` is read-only and returns safety exit code `3` when latest stable assets need mirroring or old stable assets need pruning.
- Documentation gap fixed: README now records the stable release update loop: inspect `next_action`, mirror latest stable assets, rewrite `latest.json` to fork-local URLs, refresh `SHA256SUMS.txt`, prune only old assets, and rerun `--verify-releases`.
- Evidence typo fixed: `SYNC_FORK_PAT` in the 2026-05-14 evidence file was corrected to the actual generated workflow secret name, `SYNC_PAT`.
- Build gate passed: `python -m py_compile ...`.
- Test/contract gate passed: `python -m unittest test_toolkit.py` ran 168 tests and returned `OK`.
- Diff check passed with exit code 0; Git printed LF-to-CRLF normalization warnings for touched markdown files only.
- No source code or remote GitHub state was changed.

## Rollback
- Revert the documentation commit or restore these paths from git history:
  - `README.md`
  - `docs/change-evidence/2026-05-14-fork-schedule-and-cockpit-backup.md`
  - `docs/change-evidence/2026-05-14-readme-release-retention-docs.md`
