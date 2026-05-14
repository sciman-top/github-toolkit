# 2026-05-14 fork schedule check and Cockpit Tools backup

## Scope
- Rule IDs: R1, R6, R8, E4, E5
- Risk level: medium remote GitHub metadata write
- Target: `sciman-top/cockpit-tools`
- Real GitHub write: created branch `backup/current-2026-05-14`

## Commands
- `gh auth status`
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- `python -m unittest test_toolkit.py`
- `python list-forks.py --json`
- `python sync-forks.py --verify-schedule --json`
- `python sync-forks.py --json --dry-run`
- `gh api repos/jlcodes99/cockpit-tools/releases/latest`
- `gh api repos/uppinote20/claude-dashboard/releases?per_page=5`
- `gh api repos/mack-a/v2ray-agent/releases`
- `gh api repos/sciman-top/v2ray-agent/releases`
- `gh api repos/sciman-top/cockpit-tools/git/refs -X POST -f ref=refs/heads/backup/current-2026-05-14 -f sha=5d0cd7280a12c9611fc3bbf440b5e11ef0b31a45`
- `gh api repos/sciman-top/cockpit-tools/git/ref/heads/backup/current-2026-05-14`
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
- `python sync-forks.py --add-workflow --json`
- `gh secret set SYNC_PAT --repo sciman-top/claude-dashboard`
- `gh workflow run sync-fork.yml --repo sciman-top/claude-dashboard --ref main`
- `gh workflow run sync-fork.yml --repo sciman-top/cockpit-tools --ref main`
- `gh workflow run sync-fork.yml --repo sciman-top/v2ray-agent --ref master`
- `gh api repos/actions/checkout/releases/latest --jq '{tag_name, published_at, html_url}'`
- `gh api repos/jlcodes99/cockpit-tools/releases/latest`
- `gh api repos/sciman-top/cockpit-tools/releases --paginate --jq 'length'`
- `gh api repos/jlcodes99/cockpit-tools/git/ref/tags/v0.23.2`
- `gh api repos/jlcodes99/cockpit-tools/git/tags/425f0a8c7a672157e32aa3c3803027cbca2f492b`
- `gh api repos/sciman-top/cockpit-tools/git/tags -X POST`
- `gh api repos/sciman-top/cockpit-tools/git/refs -X POST -f ref=refs/tags/v0.23.2`
- `gh release download v0.23.2 --repo jlcodes99/cockpit-tools`
- `gh release create v0.23.2 --repo sciman-top/cockpit-tools`
- `gh release upload v0.23.2 latest.json SHA256SUMS.txt --repo sciman-top/cockpit-tools --clobber`
- `gh release view v0.23.2 --repo sciman-top/cockpit-tools`
- `python sync-forks.py --verify-releases --json`
- `python sync-forks.py --verify-releases cockpit-tools --json`

## Evidence
- Authenticated user: `sciman-top`
- Live discovery: 9 owned repositories, 3 forks.
- Forks found:
  - `sciman-top/claude-dashboard` from `uppinote20/claude-dashboard`
  - `sciman-top/cockpit-tools` from `jlcodes99/cockpit-tools`
  - `sciman-top/v2ray-agent` from `mack-a/v2ray-agent`
- Schedule readiness: `ready 2/3`.
- `claude-dashboard`: missing sync workflow and `SYNC_PAT`; next action is `install_sync_workflow`.
- `cockpit-tools`: workflow current, `SYNC_PAT` present, manual smoke run succeeded at `2026-05-10T11:42:40Z`; no scheduled run has occurred yet because the workflow is new.
- `v2ray-agent`: workflow current, `SYNC_PAT` present, scheduled run succeeded at `2026-05-10T05:45:42Z`.
- Full dry-run sync reported success for 3/3 forks with no safety abort.
- Backup branch verified: `backup/current-2026-05-14` -> `5d0cd7280a12c9611fc3bbf440b5e11ef0b31a45`.
- Release mirror detection:
  - `cockpit-tools`: upstream latest stable release is `v0.23.2`, published `2026-05-12T16:36:41Z`, with 27 assets including 14 installable packages and 13 manifests/signatures; fork has no releases.
  - `claude-dashboard`: upstream latest stable release is `v1.26.2`, published `2026-05-13T07:45:13Z`, with 0 assets; release mirror is not recommended unless release notes mirroring becomes a requirement.
  - `v2ray-agent`: upstream latest stable release is `v3.5.17`, published `2026-05-12T09:23:02Z`, with 0 assets; fork has older source-only release records through `v3.5.16`, so asset mirroring is not recommended.
- Source update policy:
  - `cockpit-tools`: `stable_release_only`; default-branch `merge-upstream` is skipped by both local `sync-forks.py` and generated `sync-fork.yml`.
  - `claude-dashboard`: `upstream_default_branch`; release has no assets, so source continues to track upstream default branch.
  - `v2ray-agent`: `upstream_default_branch`; release has no assets, so source continues to track upstream default branch.
- Managed workflow rollout:
  - Added `sync-fork.yml` to `sciman-top/claude-dashboard`.
  - Updated `sync-fork.yml` on `sciman-top/cockpit-tools` and `sciman-top/v2ray-agent`.
  - Set `SYNC_PAT` secret on `sciman-top/claude-dashboard` without writing or logging the token.
  - Updated generated workflow runtime from `actions/checkout@v4` to `actions/checkout@v6`; latest verified checkout release was `v6.0.2`.
- Smoke runs:
  - `claude-dashboard`: `25814143117`, success, default-branch sync path completed.
  - `cockpit-tools`: `25814173098`, success, stable-source-only policy skipped safety/restore/diff/merge steps.
  - `v2ray-agent`: `25814196558`, success, default-branch sync path completed.
- Final schedule readiness after rollout: `ready 3/3`, `stable_source_only=1`, no missing workflow, no missing secret, no workflow drift.
- Remote workflow content verified on all three forks: `actions/checkout@v6`, `Stable source policy guard`, and `sciman-top/cockpit-tools` policy list are present.
- Release mirror rollout:
  - Initial fork release count for `sciman-top/cockpit-tools` was `0`.
  - Mirrored upstream stable tag `v0.23.2` from annotated tag object `425f0a8c7a672157e32aa3c3803027cbca2f492b`; fork tag object is `917452c7b10ce1e882e173fccaa2e8be30f4f0d5`.
  - Created fork release `https://github.com/sciman-top/cockpit-tools/releases/tag/v0.23.2`.
  - Uploaded 27 release assets; original asset sizes/digests matched upstream before metadata rewrite.
  - Rewrote fork `latest.json` download URLs from `jlcodes99/cockpit-tools` to `sciman-top/cockpit-tools`.
  - Updated fork `SHA256SUMS.txt` so the `latest.json` hash matches the rewritten file.
  - Final fork release verification: `asset_count=27`, `isDraft=false`, `isPrerelease=false`, total asset bytes `517243636`.
  - Remote `latest.json` verification: contains `sciman-top/cockpit-tools`, does not contain `jlcodes99/cockpit-tools`, and SHA256 `c0838fd2ce1e79db3551f2bdcb715fa27e239b2aad9e529f84256c6eef83fe84` matches the fork `SHA256SUMS.txt` line.
- Release retention policy:
  - Policy is `latest_stable_assets_only`.
  - Latest stable release keeps install/build/package assets, signatures, checksums, and `latest.json`.
  - Older stable releases keep only tag/release title/body notes; old stable release assets must be deleted.
  - Added `sync-forks.py --verify-releases` as a read-only gate for this policy.
  - Final all-fork release retention verification: `ready 3/3`, `latest_stable_assets_required=1`, `latest_stable_missing_or_without_assets=0`, `old_stable_assets_present=0`.
  - `cockpit-tools` release retention verification: `ready 1/1`, latest stable `v0.23.2`, fork latest stable asset count `27`, old stable asset count `0`.

## Rollback
- Delete only the fixed backup branch if it was created in error:
  `gh api repos/sciman-top/cockpit-tools/git/refs/heads/backup/current-2026-05-14 -X DELETE`
- Do not delete `backup/pre-sync`; that branch is maintained by the sync workflow as a moving restore point.
- Delete the mirrored release if it was created in error:
  `gh release delete v0.23.2 --repo sciman-top/cockpit-tools --cleanup-tag --yes`
