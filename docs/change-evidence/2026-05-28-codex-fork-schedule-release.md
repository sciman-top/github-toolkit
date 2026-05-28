# 2026-05-28 codex fork schedule and release evidence

- Rule IDs: R1, R2, R4, R6, R8, E4, E5, E6
- Risk level: medium remote GitHub maintenance
- Current location -> target: `D:\CODE\github-toolkit` -> `sciman-top/codex`
- Real GitHub writes:
  - Added `.github/workflows/sync-fork.yml` to `sciman-top/codex`
  - Set `SYNC_PAT` secret for `sciman-top/codex` without printing or storing the token
  - Created tag/release `rust-v0.134.0` in `sciman-top/codex`
  - Mirrored 137 upstream release assets from `openai/codex`
- No local or repository token files were created.

## Commands and key evidence

```powershell
python sync-forks.py codex --add-workflow --json
```

Key output: added sync workflow at `https://github.com/sciman-top/codex/blob/main/.github/workflows/sync-fork.yml`.

```powershell
gh workflow run sync-fork.yml --repo sciman-top/codex --ref main
gh run watch 26580574387 --repo sciman-top/codex --exit-status
```

Key output: workflow dispatch run `https://github.com/sciman-top/codex/actions/runs/26580574387` completed successfully in 20 seconds.

```powershell
python sync-forks.py --verify-schedule --json
```

Key output: `Schedule readiness verified: ready 4/4`; `codex` has `workflow_installed=true`, `workflow_current=true`, `secret_sync_pat_present=true`, and a successful smoke run.

```powershell
python sync-forks.py --verify-releases --json
```

Key output: `Release retention verified: ready 4/4`; `codex` latest stable `rust-v0.134.0` has `fork_latest_stable_asset_count=137` and `next_action=none`.

```powershell
$upstreamAssets = (gh release view rust-v0.134.0 --repo openai/codex --json assets | ConvertFrom-Json).assets.name
$forkAssets = (gh release view rust-v0.134.0 --repo sciman-top/codex --json assets | ConvertFrom-Json).assets.name
```

Key output: upstream asset count `137`, fork asset count `137`, missing count `0`, extra count `0`.

## Rollback

- Default branch sync rollback: `backup/pre-sync` was created/refreshed by the successful sync workflow before merging upstream.
- Workflow rollback: remove or revert `.github/workflows/sync-fork.yml` in `sciman-top/codex`.
- Secret rollback: delete `SYNC_PAT` from `sciman-top/codex` repository secrets.
- Release rollback: delete fork release `rust-v0.134.0`; if needed also delete fork tag `refs/tags/rust-v0.134.0`.
