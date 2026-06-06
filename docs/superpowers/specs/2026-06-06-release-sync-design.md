# Weekly Stable Release Sync Design

## Goal

为 `github-toolkit` 增加“通用稳定版 release 镜像自动收敛”能力：

- 支持本地手动执行 release 收敛
- 支持下发到 fork 仓库的 weekly workflow 自动收敛
- 只覆盖 `latest_stable_assets_only`
- 明确排除 `sciman-top/cockpit-tools-local` 的 `self_use_local_releases`

## In Scope

- 新增 `sync-forks.py --sync-releases`
- 复用现有 `--verify-releases` 的策略判定结果
- 自动处理：
  - 创建缺失的最新稳定版 release
  - 补齐最新稳定版缺失资产
  - 清理旧稳定版上的安装/打包资产
- 更新生成的 `sync-fork.yml`，让 fork 仓库每周自动执行 release 收敛
- 收紧 release 核验，至少确保“上游最新稳定版资产名集合”被 fork 最新稳定版完整覆盖

## Out of Scope

- 不自动更新 `cockpit-tools-local` 的自用版 weekly release
- 不改变 `self_use_local_releases` 的策略语义
- 不引入新的仓库配置文件

## Design

### Local CLI

`sync-forks.py` 新增 `MODE_SYNC_RELEASES` / `--sync-releases`：

- `source_only_release_notes`：直接跳过
- `self_use_local_releases`：直接跳过
- `latest_stable_assets_only`：
  - 如果 fork 缺少最新稳定版 release，则创建同 tag release
  - 如果 fork 最新稳定版缺少上游资产，则下载上游资产并上传到 fork
  - 如果 fork 旧稳定版仍保留安装/打包资产，则删除这些旧资产
  - 完成后重新核验；未收敛则返回 `EXIT_SAFETY`

### Workflow

下发到 fork 的 `sync-fork.yml` 新增 release retention job/step：

- 在源码同步逻辑之后执行
- 只在 `latest_stable_assets_only` 场景生效
- 使用 `gh api` + `gh release download/upload/delete-asset`
- 对 `self_use_local_releases` 和 `source_only_release_notes` 明确输出 skip notice

### Verification Tightening

现有 `--verify-releases` 从“看资产数量”升级为：

- 先判断上游是否存在安装/打包资产
- 若存在，则检查 fork 最新稳定版是否存在
- 若存在，则检查 fork 最新稳定版是否覆盖上游最新稳定版的全部资产名
- 若旧稳定版仍带安装/打包资产，继续返回 `prune_old_stable_release_assets`

## Risks

- release 上传/删除是中风险远端写操作，必须保留 dry-run 和 verify 模式分层
- fork workflow 中无法复用本地 Python 逻辑，因此需要在 workflow 模板中维护一份等价执行逻辑
- 如果未来某上游依赖 `latest.json` 内容改写和 `SHA256SUMS.txt` 联动，后续可能需要再补精细化资产内容处理

## Acceptance

- `python sync-forks.py --sync-releases --dry-run --json` 能输出稳定的 JSON 摘要
- `python sync-forks.py --verify-releases --json` 能识别“缺少最新稳定版 release / 缺少最新资产 / 旧稳定版残留资产”
- 生成的 workflow 模板包含 weekly release 收敛步骤
- `cockpit-tools-local` 继续跳过自动自用版 weekly release
