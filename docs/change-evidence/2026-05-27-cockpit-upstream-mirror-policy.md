# 2026-05-27 cockpit upstream mirror policy

- 规则 ID: R4, R8, E4, E6
- 风险等级: medium_remote_write
- 当前落点: `D:\CODE\github-toolkit`
- 目标归宿: `sciman-top/cockpit-tools-local` 远端只保留 `main` 和可移动 `mirror/upstream-main`；官方上游源码镜像只使用 `mirror/upstream-main`，不再长期保留 `backup/*` 固定恢复点。
- 真实 GitHub 写操作: yes

## Commands

```powershell
gh api repos/sciman-top/cockpit-tools/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"
gh api repos/sciman-top/cockpit-tools/git/ref/heads/mirror/upstream-main --jq "{ref:.ref, sha:.object.sha}"
gh api repos/jlcodes99/cockpit-tools/branches/main --jq "{name:.name, sha:.commit.sha, date:.commit.commit.committer.date}"
gh api -X DELETE repos/sciman-top/cockpit-tools/git/refs/heads/backup/upstream-main-20260518-201303
gh api -X PATCH repos/sciman-top/cockpit-tools/git/refs/heads/mirror/upstream-main -f sha=866f526643e065fb9682cc0b73060f781235a20e -F force=true --jq "{ref:.ref, sha:.object.sha}"
gh api -X DELETE repos/sciman-top/cockpit-tools-local/git/refs/heads/backup/current-2026-05-14
gh api -X DELETE repos/sciman-top/cockpit-tools-local/git/refs/heads/backup/fork-main-before-selfuse-20260518-201303
gh api -X DELETE repos/sciman-top/cockpit-tools-local/git/refs/heads/backup/pre-sync
gh api -X DELETE repos/sciman-top/cockpit-tools-local/git/refs/heads/backup/selfuse-before-main-20260518-201303
```

## Key Output

- Deleted fixed upstream restore branch: `refs/heads/backup/upstream-main-20260518-201303`
- Previous mirror SHA: `3c44e7fb37714c3a2b2056f4949fdbe3630fd7e5`
- New mirror SHA: `866f526643e065fb9682cc0b73060f781235a20e`
- Upstream source: `jlcodes99/cockpit-tools/main`
- Deleted remaining remote backup refs after policy tightening:
  - `refs/heads/backup/current-2026-05-14`
  - `refs/heads/backup/fork-main-before-selfuse-20260518-201303`
  - `refs/heads/backup/pre-sync`
- `refs/heads/backup/selfuse-before-main-20260518-201303`

## Verification

2026-05-28 final readback:

- Remote branches now present:
  - `refs/heads/main` -> `5459248a9430967f9f0849b81e9ef49e68cb74d0`
  - `refs/heads/mirror/upstream-main` -> `866f526643e065fb9682cc0b73060f781235a20e`
- `gh api repos/sciman-top/cockpit-tools-local/git/matching-refs/heads/backup --jq ".[] | {ref:.ref, sha:.object.sha}"` returned no refs.
- `python sync-forks.py --verify-schedule cockpit-tools-local --json`: `ready 1/1`, `next_action=none`, `stable_source_only=1`.
- `python sync-forks.py --verify-releases cockpit-tools-local --json`: `ready 1/1`, `next_action=none`, `release_policy=self_use_local_releases`.
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`: passed.
- `python -m unittest test_toolkit.py`: passed, 170 tests OK.
- `python -m unittest test_toolkit.py` as contract/invariant gate: passed, 170 tests OK.

## Rollback

If the mirror refresh itself must be rolled back, move `refs/heads/mirror/upstream-main` back to the recorded previous SHA:

```powershell
gh api -X PATCH repos/sciman-top/cockpit-tools/git/refs/heads/mirror/upstream-main -f sha=3c44e7fb37714c3a2b2056f4949fdbe3630fd7e5 -F force=true
```

Do not recreate `backup/upstream-main-<timestamp>` or other `backup/*` branches unless the user explicitly asks for a temporary fixed recovery branch in that task.
