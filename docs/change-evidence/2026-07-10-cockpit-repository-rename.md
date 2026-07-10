# 2026-07-10 cockpit 仓库更名

## 目标与风险

- 规则 ID：R1、R2、R4、R7、R8、E4、E6
- 风险等级：中风险远端仓库元数据变更
- 当前落点：`sciman-top/cockpit-tools-local`
- 目标归宿：`sciman-top/cockpit-tools`
- 用户授权：明确要求仓库名称与上游 `cockpit-tools` 保持一致
- 仓库 ID：`1233638944`，用于证明 rename 前后为同一仓库

## 执行与验证

- GitHub API rename：`PATCH repos/sciman-top/cockpit-tools-local`，`name=cockpit-tools`
- 新地址：<https://github.com/sciman-top/cockpit-tools>
- 旧地址：<https://github.com/sciman-top/cockpit-tools-local>，仍重定向到同一仓库 ID
- 默认分支：`main`
- 上游：`jlcodes99/cockpit-tools`
- 分支：`main` 与 `backup/pre-sync`，SHA 均为 `a9201c00fd0c773def92a0f87cbd75c89501c44a`
- `SYNC_PAT`：rename 后仍存在
- schedule readiness：`ready=1/1`，`next_action=none`
- release readiness：`ready=1/1`，`next_action=none`
- 最新稳定版：`v1.1.4`，27 个资产完整保留
- 源码状态：`behind_by=0`
- rename 后 smoke：<https://github.com/sciman-top/cockpit-tools/actions/runs/29086354037>，success

## 回滚

- 使用 GitHub API 将仓库 `name` 改回 `cockpit-tools-local`。
- rename 不改写 commit、分支、release、secret 或 Actions 历史，无数据迁移回滚动作。

## 兼容与供应链

- E5：未变更依赖或外部工具版本。
- E6：GitHub 旧 URL 提供自动重定向；脚本通过实时 fork discovery 发现新名称，JSON 字段结构不变。
