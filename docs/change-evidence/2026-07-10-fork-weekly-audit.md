# 2026-07-10 fork 每周同步与稳定版 release 审计

## 范围与结论

- 规则 ID：R1、R2、R3、R4、R6、R7、R8、E4、E5、E6
- 风险等级：中风险远端写入（受管 workflow 更新、镜像分支恢复、Actions 运行）
- 账号：`sciman-top`
- 实时发现：账号 owner 视图共 14 个仓库，其中 fork 3 个
- 最终结论：3 个 fork 均 `behind_by=0`，schedule readiness `ready=3/3`，release retention readiness `ready=3/3`
- 真实 GitHub 写操作：是；未创建、上传或删除 release 资产

## 根因与变更

1. `sciman-top/cockpit-tools-local:mirror/upstream-main` 与上游分叉，2026-07-05 的 schedule run 在 `merge-upstream` 返回 HTTP 409。
2. 先记录旧 SHA `99f252280a8df5b086f0f64d12f222effe172609`，再将镜像分支对齐到上游 SHA `93d939b77055365933ec995ad5042c4e67601169`。
3. 重新安装 workflow 后发现模板把嵌入 Python 的 `line.rstrip("\\n")` 渲染为跨行字符串，GitHub 将 YAML 判为无效并拒绝 `workflow_dispatch`。
4. 修复 `sync-forks.py` 的转义并增加回归测试，随后按显式 force 确认重写 3 个 fork 的受管 `.github/workflows/sync-fork.yml`。
5. 首次推送后的 CI 暴露既有 gate 漂移：已删除的 `.claude/hooks/governed-pre-tool-use.py` 仍在 compile 清单中；将 CI 对齐到项目现行 build 命令。

## 验证证据

- build：`python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py`，exit 0
- test：`python -m unittest test_toolkit.py`，189 tests，OK
- contract/invariant：再次执行 `python -m unittest test_toolkit.py`，189 tests，OK
- hotspot：`python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`，`violations=[]`
- fork 状态：`python list-forks.py --json`，3 个 fork 均 `behind_by=0` 且 workflow installed
- schedule：`python sync-forks.py --json --verify-schedule`，`ready=3/3`、`scheduled_run_missing_or_unhealthy=0`
- release：`python sync-forks.py --json --verify-releases`，`ready=3/3`、所有 `next_action=none`
- release 策略：`claude-dashboard` 与 `v2ray-agent` 为 `source_only_release_notes`；`cockpit-tools-local` 为 `self_use_local_releases`，因此本次没有自动镜像 release 资产

## Actions 运行

- `claude-dashboard` smoke：<https://github.com/sciman-top/claude-dashboard/actions/runs/29082309813>，success
- `cockpit-tools-local` smoke：<https://github.com/sciman-top/cockpit-tools-local/actions/runs/29082309600>，success
- `v2ray-agent` smoke：<https://github.com/sciman-top/v2ray-agent/actions/runs/29082309629>，success
- `cockpit-tools-local` schedule 重跑：<https://github.com/sciman-top/cockpit-tools-local/actions/runs/28731717482>，success

## 回滚

- 本地代码：对本次提交执行 `git revert <commit>`，再重新下发上一版受管 workflow。
- `cockpit-tools-local` 镜像分支恢复：将 `refs/heads/mirror/upstream-main` 强制恢复到旧 SHA `99f252280a8df5b086f0f64d12f222effe172609`；当前审计后 head 为 `49fecc5a00eb7efdabee3e3af4e61d38466345cf`。
- workflow 更新恢复：分别 revert 3 个 fork 默认分支上的最新 `ci: update fork sync workflow with safety checks` 提交。
- release：本次无 release 写入，无 release 回滚动作。

## 供应链与数据结构

- E5：未新增或升级 Python、GitHub Actions、`gh` 或第三方依赖。
- E6：未改变 GitHub API 字段、workflow 输入输出协议、同步状态 JSON 或持久化数据结构。
