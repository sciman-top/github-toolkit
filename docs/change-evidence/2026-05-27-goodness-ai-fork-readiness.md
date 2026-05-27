# 2026-05-27 goodness-ai fork readiness repair

## Scope
- Rule IDs: R1, R2, R4, R6, R8, E4, E5, E6
- Risk level: medium remote persistence
- Target: `sciman-top/goodness-ai`
- Upstream: `DAOMAEVO/goodness-ai`
- Goal: bring the newly discovered fork under weekly upstream sync tracking and latest stable release retention.

## Remote writes
- Installed managed workflow: `https://github.com/sciman-top/goodness-ai/blob/main/.github/workflows/sync-fork.yml`
- Set GitHub Actions secret: `SYNC_PAT`
- Triggered smoke workflow: `https://github.com/sciman-top/goodness-ai/actions/runs/26511064796`
- Created fork release: `https://github.com/sciman-top/goodness-ai/releases/tag/v0.1.6`

## Commands and key evidence
- `python sync-forks.py --add-workflow goodness-ai --json`
  - Result: workflow added on `main`.
- `gh workflow run sync-fork.yml --repo sciman-top/goodness-ai --ref main`
  - Result: run `26511064796`.
- `gh run watch 26511064796 --repo sciman-top/goodness-ai --exit-status`
  - Result: success; safety check, restore point, diff guard, and merge upstream all completed.
- `python sync-forks.py --verify-schedule --json`
  - Result: `ready 4/4`; `goodness-ai` has workflow current, `SYNC_PAT` present, smoke run success, `next_action=none`.
- `python sync-forks.py --verify-releases --json`
  - Result: `ready 4/4`; `goodness-ai` mirrors upstream stable `v0.1.6` with two fork-local installable assets.
- `python list-forks.py --json`
  - Result: `repo_count=10`, `fork_count=4`; `goodness-ai behind_by=0`.

## Release mirror details
- Upstream stable release: `DAOMAEVO/goodness-ai@v0.1.6`
- Fork release: `sciman-top/goodness-ai@v0.1.6`
- Tag target: `c843321507662f6f6bf2c9611d34c2bd22b6bd9d`
- Mirrored assets:
  - `GoodnessAI-0.1.6_x64-setup.exe`
    - size: `5420890`
    - sha256: `3718ef1b7fb49958bf9820a90a3e98a8fb63ada5b3431337e2628a1fe0b55050`
  - `GoodnessAI-Setup-v0.1.6.msi`
    - size: `7634944`
    - sha256: `c99812a0e0705a9b4f787cef5b887a304061cec07f72abaed6e14d8845c15720`

## Rollback
- Workflow rollback: delete or revert `.github/workflows/sync-fork.yml` in `sciman-top/goodness-ai`.
- Secret rollback: remove the repository Actions secret `SYNC_PAT`.
- Release rollback: delete `sciman-top/goodness-ai` release `v0.1.6` and tag `refs/tags/v0.1.6` if the release should no longer be mirrored.
- Source rollback: use `backup/pre-sync` created by the smoke workflow before the upstream merge.

## Notes
- No token values were written into this evidence file.
- `goodness-ai` has no prior fork stable releases, so no old installable assets needed pruning.
