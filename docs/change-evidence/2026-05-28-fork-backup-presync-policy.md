# 2026-05-28 fork backup/pre-sync policy

- 规则 ID: R4, R8, E4, E6
- 风险等级: low_policy_doc
- 当前落点: `D:\CODE\github-toolkit`
- 目标归宿: 统一 fork 恢复点口径：`upstream_default_branch` 仓库保留一个移动 `backup/pre-sync`；`stable_release_only` / self-use / mirror-only 仓库不长期保留 `backup/*`。
- 真实 GitHub 写操作: no

## Commands

```powershell
python list-forks.py --json
python sync-forks.py --verify-schedule --json
gh api repos/sciman-top/claude-dashboard/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"
gh api repos/sciman-top/goodness-ai/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"
gh api repos/sciman-top/v2ray-agent/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"
gh api repos/sciman-top/cockpit-tools-local/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"
```

## Key Output

- `python list-forks.py --json`: discovered 4 forks.
- `python sync-forks.py --verify-schedule --json`: `ready 4/4`, `stable_source_only=1`.
- `sciman-top/claude-dashboard`: `source_update_policy=upstream_default_branch`, has `refs/heads/backup/pre-sync`.
- `sciman-top/goodness-ai`: `source_update_policy=upstream_default_branch`, has `refs/heads/backup/pre-sync`.
- `sciman-top/v2ray-agent`: `source_update_policy=upstream_default_branch`, has `refs/heads/backup/pre-sync`.
- `sciman-top/cockpit-tools-local`: `source_update_policy=stable_release_only`, `matching-refs/heads/backup` returned no refs.

## Policy

Keep `backup/pre-sync` only where the workflow can perform a real default-branch `merge-upstream`. Do not require it for stable-source-only or mirror-only forks because those paths skip `merge-upstream` and use explicit mirror branch SHA evidence instead.

## Rollback

This change is documentation/policy only. Roll back by reverting the README / AGENTS / evidence commit. No remote refs were created, deleted, or moved for this policy note.

## Verification

- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`: passed.
- `python -m unittest test_toolkit.py`: passed, 170 tests OK.
- `python -m unittest test_toolkit.py` as contract/invariant gate: passed, 170 tests OK.
- `python sync-forks.py --verify-schedule --json`: `ready 4/4`, `stable_source_only=1`.
- `git diff --check` in `D:\CODE\github-toolkit`: passed; only LF/CRLF warnings for edited Markdown files.
