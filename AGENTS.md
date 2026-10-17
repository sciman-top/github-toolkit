# AGENTS.md - github-toolkit
**项目契约**: 2.0
**全局规则复核**: 9.90
**最后更新**: 2026-10-17

## 1. 当前落点与目标归宿
- 当前落点：`gh_utils.py` 负责 GitHub CLI/API 与认证，`sync-forks.py` 负责 dry-run、安全检查、恢复点、同步和 release 收敛，`list-forks.py` 负责只读状态。
- 目标归宿：以最少接口安全维护 fork；真实写入只沿“发现 -> dry-run -> 删除保护 -> `backup/pre-sync` -> apply -> 状态复核”主链发生。
- 仓、SHA、认证和线上结果必须 fresh query，不写入规则快照。

## A. 仓库事实与模块边界
- 真实同步必须先 dry-run；`--force` 真实写入还需 `SYNC_FORKS_FORCE_ACK=I_UNDERSTAND`。
- 默认分支同步前创建移动恢复点 `backup/pre-sync`；release 同步保留执行前后的 API 状态作为回滚依据。
- `GH_TOKEN`、`GITHUB_TOKEN` 与本机 `gh auth` 是认证输入，不得写入仓库、测试输出或提交信息。
- 不为单一仓型增加策略层；只有出现第二个真实消费者且现有主链无法表达时才增加 seam。

## B. 执行边界与参考依据
- 默认主代理串行完成；子代理委派与并行已获用户常驻授权（见全局规则 A.2），每次委派仍须限定独立切片、互斥 write set、最低证明与 stop。
- setup/auth 不是门禁；认证缺失时只做本地测试，不声称 live 状态。
- workflow、默认分支和 release 的真实写入属于中风险；未确认 dry-run、删除阈值和恢复点时阻断。
- 本仓无专属 reference shelf；出现版本敏感 API 或第三方源码消费者时再登记来源、revision、license 与 consumer。
- 不保留逐任务证据文件、计划副本、合成 benchmark 或宿主环境修复层；Git 历史、当前命令输出和 GitHub Actions run 是证据入口。

## C. 最低充分门禁与回滚
- fixed order：`build -> test -> contract/invariant -> hotspot`。
- build：无独立构建产物；test 会导入并执行全部 Python 模块。
- test：`python -m unittest -q test_toolkit.py`。
- contract/invariant：同一 suite 覆盖 dry-run、删除保护、恢复点、force acknowledgement、JSON 错误和 release 安全结果；禁止再叠加重复 suite。
- hotspot：当前无实测性能故障；仅在出现可复现性能回归时添加一次针对性测量。
- `.github/workflows/integration-api-smoke.yml` 是定时只读运营检查，不是每次编码收口门禁。
- 测试失败、删除保护失效或恢复点不成立时阻断真实写入。

## D. Git 与回滚
- Git baseline=`main`; upstream=`origin/main`; closeout=`focused_test_only`。
- 回滚只撤销本任务；远端同步按 `backup/pre-sync` 或执行前 SHA 恢复。
