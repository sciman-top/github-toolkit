# 2026-07-10 cockpit-tools-local 上游 main 迁移

## 目标与风险

- 规则 ID：R1、R2、R3、R4、R6、R7、R8、E4、E6
- 风险等级：高风险远端分支迁移与旧分支删除
- 当前落点：`mirror/upstream-main@49fecc5a00eb7efdabee3e3af4e61d38466345cf` 为默认分支
- 目标归宿：仅保留 `main`，其源码基线对齐 `jlcodes99/cockpit-tools:main@93d939b77055365933ec995ad5042c4e67601169`
- 用户授权：明确要求清除自用版代码、只保留上游源码、默认分支改为 `main`

## 预演与执行顺序

1. 记录旧默认分支、旧 SHA、上游 SHA、分支保护与 release 清单。
2. 本地移除 cockpit 的 self-use、mirror-only 和 self-use release 策略，删除专用维护脚本。
3. 执行 `build -> test -> contract/invariant -> hotspot`。
4. 创建 `main` 指向上游 SHA，确认 tree/commit 一致后切换默认分支。
5. 下发受管 `sync-fork.yml`，触发 smoke run 并复核 schedule/release readiness。
6. 删除 `mirror/upstream-main`，确认远端只保留 `main`。

## 回滚

- 在旧 SHA 可达期间，可重新创建 `mirror/upstream-main` 指向 `49fecc5a00eb7efdabee3e3af4e61d38466345cf` 并将其设回默认分支。
- 删除 `main` 前必须先把默认分支切回恢复分支。
- 本地策略与文档通过 revert 本次 github-toolkit 提交恢复。
- 本次迁移前远端没有 release，因此没有 release 数据回滚动作。

## 供应链与数据结构

- E5：未新增、删除或升级第三方依赖。
- E6：默认分支从 `mirror/upstream-main` 迁移为 `main`；workflow 路径和 JSON 输出字段保持兼容。

## 执行结果

- 新默认分支：`main@a9201c00fd0c773def92a0f87cbd75c89501c44a`
- 上游源码基线：`jlcodes99/cockpit-tools:main@93d939b77055365933ec995ad5042c4e67601169`
- `main` 比上游多出的内容仅为受管 `.github/workflows/sync-fork.yml` 提交，不含自用业务代码。
- 已删除：`mirror/upstream-main@49fecc5a00eb7efdabee3e3af4e61d38466345cf`
- 保留恢复点：`backup/pre-sync@a9201c00fd0c773def92a0f87cbd75c89501c44a`
- smoke run：<https://github.com/sciman-top/cockpit-tools-local/actions/runs/29083373609>，success
- schedule readiness：`ready=1/1`，`source_update_policy=upstream_default_branch`，`next_action=none`
- release readiness：`ready=1/1`，`release_policy=latest_stable_assets_only`，`next_action=none`
- 最新稳定版：`v1.1.4`；上游 27 个资产已完整镜像，旧稳定版资产残留为 0
- 真实 GitHub 写操作：创建 `main`、切换默认分支、下发 workflow、创建/同步稳定版 release、删除旧镜像分支
