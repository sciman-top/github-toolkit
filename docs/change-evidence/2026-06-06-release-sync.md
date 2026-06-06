# 2026-06-06 Release Sync Evidence

- 规则 ID：R1 / R2 / R6 / R8 / E4 / E5 / E6
- 风险等级：中风险（涉及 GitHub release 创建、上传、删除）
- 当前落点：`sync-forks.py` 仅支持 `--verify-releases` 只读门禁
- 目标归宿：补齐 `latest_stable_assets_only` 的手动与每周自动 release 收敛；明确排除 `sciman-top/cockpit-tools-local` 自用版 weekly release

## 执行命令

- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- `python -m unittest test_toolkit.py`
- `python sync-forks.py --json --verify-releases`
- `python sync-forks.py --json --sync-releases --dry-run`

## 关键变化

- 新增 `--sync-releases` 模式，复用 release retention 策略做实际远端收敛
- `--verify-releases` 从“粗看最新 release 是否有资产”收紧到“检查上游要求镜像的资产名集合是否被完整覆盖”
- 生成的 `sync-fork.yml` 新增每周 release retention 执行步骤
- `self_use_local_releases` 仍然只做跳过，不自动更新 `cockpit-tools-local` 自用版 weekly release

## 回滚入口

- 代码回滚：使用 git 历史回退本次变更
- 远端 release 回滚：
  - 若 fork 新建了不需要的最新稳定版 release：手动删除该 release
  - 若错误上传了 release 资产：用 `gh release delete-asset <tag> <asset-name> -R owner/repo --yes`
  - 若错误删除了旧稳定版安装资产：从 upstream 对应稳定版重新下载并重新上传
