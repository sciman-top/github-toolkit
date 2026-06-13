## 2026-06-06 Delete `sciman-top/codex`

- rule_ids: `R1,R4,R8,E5,E6`
- risk_level: `high`
- operator: `Codex`
- target_repo: `sciman-top/codex`
- intent: `用户明确要求删除不再需要的 fork`

### Pre-delete snapshot

- repo: `https://github.com/sciman-top/codex`
- fork_of: `openai/codex`
- default_branch: `main`
- created_at: `2026-05-28T13:36:36Z`
- updated_at: `2026-06-06T06:48:33Z`
- pushed_at: `2026-06-06T06:48:28Z`
- main_sha: `760e419b7700b8cb27cf770f17e0d056c0b3b0fd`
- backup_pre_sync_sha: `71cd9fadd5c473bf989a3bb92d8879e7f3a316a7`
- latest_fork_release: `rust-v0.134.0`
- latest_fork_release_url: `https://github.com/sciman-top/codex/releases/tag/rust-v0.134.0`
- latest_upstream_stable_at_last_audit: `rust-v0.137.0`
- upstream_url: `https://github.com/openai/codex`

### Commands

```powershell
gh api repos/sciman-top/codex
gh api repos/sciman-top/codex/branches?per_page=20
gh api repos/sciman-top/codex/releases?per_page=10
gh api -X DELETE repos/sciman-top/codex
python list-forks.py --json
python sync-forks.py --json --verify-schedule
python sync-forks.py --json --verify-releases
```

### Post-delete verification

- `gh api repos/sciman-top/codex` returned `404 Not Found`.
- `python list-forks.py --json` converged to `repo_count=9`, `fork_count=3`.
- Remaining tracked forks after deletion:
  - `sciman-top/claude-dashboard`
  - `sciman-top/cockpit-tools-local`
  - `sciman-top/v2ray-agent`
- `python sync-forks.py --json --verify-schedule` returned `ready=3/3`.
- `python sync-forks.py --json --verify-releases` returned `ready=3/3`.

### Rollback path

- Recreate the fork from `openai/codex`.
- If branch state is needed, restore `main` from `760e419b7700b8cb27cf770f17e0d056c0b3b0fd`.
- `backup/pre-sync` only survives if an external git backup still exists; after remote deletion, GitHub-hosted refs and mirrored release assets are not guaranteed recoverable.
- If release mirroring is needed again, re-run the release mirror flow against upstream stable releases after reforking.
