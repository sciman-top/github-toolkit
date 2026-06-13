# 2026-06-13 README current-state refresh

- 规则 ID：R1 / R2 / R6 / R8 / E4 / E5 / E6
- 风险等级：low_documentation
- 当前落点：`README.md` 已覆盖大部分 fork 同步与 release retention 说明，但缺少 2026-06-13 live 状态快照、本地硬门禁入口，以及当前 integration smoke workflow 的真实执行内容
- 目标归宿：把 README 和相关文档入口同步到当前代码、workflow 与 live GitHub 状态，同时保留历史证据文件的时间点语义
- 真实 GitHub 写操作：no

## 执行命令

```powershell
git status --short
git log --oneline -8
python sync-forks.py --help
python list-forks.py --help
python list-forks.py --json
python sync-forks.py --json --verify-schedule
python sync-forks.py --json --verify-releases
python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py
python -m unittest test_toolkit.py
python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check
git diff --check
```

## 关键证据

- `python list-forks.py --json`：
  - `auth.mode=gh_login`
  - `discovery.repo_count=9`
  - `discovery.fork_count=3`
  - 当前跟踪 fork 为 `sciman-top/claude-dashboard`、`sciman-top/cockpit-tools-local`、`sciman-top/v2ray-agent`
- `python sync-forks.py --json --verify-schedule`：
  - `ready=3/3`
  - `mirror_only_source=1`
  - 三个 fork 的 `next_action` 均为 `none`
- `python sync-forks.py --json --verify-releases`：
  - `ready=3/3`
  - `latest_stable_assets_required=0`
  - `upstream_mirror_assets_present=0`
  - `cockpit-tools-local` 当前 `release_policy=self_use_local_releases` 且 `next_action=none`
- `.github/workflows/integration-api-smoke.yml` 当前除了 `list-forks.py --json` 与 `sync-forks.py --json --dry-run` 外，还会执行：
  - `python -m py_compile ...`
  - `python sync-forks.py --json --verify-releases`
  - `python sync-forks.py --json --sync-releases --dry-run`
  - 指定仓库的 release 相关 dry-run
  - `integration-*.json` 校验与 artifact 上传
- `README.md` 已补充：
  - 2026-06-13 的 live 状态快照
  - 当前 3 个 fork 的 source/release 策略表
  - `--verify-schedule` / `--verify-releases` 的 safety exit code `3` 语义
  - 本地硬门禁顺序
  - 相关文档索引

## 验证

- build gate：`python -m py_compile ...` 通过
- test gate：`python -m unittest test_toolkit.py` 通过，`Ran 182 tests ... OK`
- contract/invariant gate：当前与 test 共用 `test_toolkit.py`，通过，`Ran 182 tests ... OK`
- hotspot gate：`python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check` 通过
- `git diff --check`：退出码 `0`；仅提示 `README.md` 与现有 `scripts/Update-CockpitSelfUseFork.ps1` 的 LF/CRLF 归一化警告

## 回滚

- 文档回滚：从 git 历史恢复以下文件
  - `README.md`
  - `docs/change-evidence/2026-06-13-readme-current-state-refresh.md`
- 本次未执行真实 GitHub 写操作，无需远端回滚
