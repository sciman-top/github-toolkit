# GitHub Toolkit

Fork 仓库自动同步管理工具集，带安全防护机制。

## 这个工具做什么
- 为 fork 仓库添加自动同步 workflow
- 立即同步单个或全部 fork
- 列出 fork 仓库及其同步状态
- 在同步前做安全检查，避免上游仓库异常时把 fork 一起带崩

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `setup.bat` | 检测/安装 `gh` CLI，完成登录和权限准备 |
| `sync-forks.py` | 添加同步 workflow、检查定时同步 readiness，或执行同步，支持 `--json` |
| `list-forks.py` | 查看所有 fork 和同步状态，支持 `--json` |
| `gh_utils.py` | 公共 GitHub 辅助逻辑 |
| `scripts/Verify-WindowsProcessEnvironment.ps1` | 诊断 Codex/PowerShell 进程环境与 `gh` 可用性 |

## 安全机制
同步前会自动做一组保护检查：

- 确认上游仓库仍然存在且可访问
- 比较上游与 fork 的文件数量
- 检查 upstream 的删除量，拦截关键路径删除
- 如果上游文件数低于 fork 的 50%，自动中止同步
- 默认分支同步仓库每次 `merge-upstream` 前写入一个移动 `backup/pre-sync` 恢复点（手动同步与 GitHub Actions 一致）
- GitHub Actions 定时同步也会执行同样的检查
- 对安装型工具可启用 stable-source-only 策略，阻止默认分支被每周 `merge-upstream` 自动覆盖，改为后续镜像稳定版 release/tag

`--force` 默认需要显式确认环境变量后才能执行真实写操作（包括真实同步和强制重写 workflow；`--dry-run` 不受此限制）：

```powershell
$env:SYNC_FORKS_FORCE_ACK = "I_UNDERSTAND"
python sync-forks.py --force repo-name
```

## 快速开始
### 1. 首次使用
双击运行 `setup.bat`，会自动完成：
- 检测或安装 GitHub CLI
- 浏览器授权登录
- 配置 workflow 所需权限

`setup.bat` 会先恢复 Windows 基础进程环境变量（`SystemRoot`、`WINDIR`、`ComSpec`、`APPDATA`、`LOCALAPPDATA`、`PROGRAMDATA`、`ProgramFiles`），并从安全白名单补入代理变量（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`）。
这是为了避免某些 Agent/Codex shell 缺少基础变量或代理变量时，`gh` 找不到本地 keyring、配置目录或网络栈，从而被误判为“未登录”或“DNS/网络失败”。

### 2. 给 fork 添加自动同步
```powershell
python sync-forks.py --add-workflow
python sync-forks.py --add-workflow repo-name
python sync-forks.py --add-workflow --dry-run
```

添加后，GitHub Actions 会在每周日 UTC 02:17，也就是北京时间 10:17 自动同步。
该时间刻意避开 GitHub Actions 整点高峰，降低 scheduled workflow 延迟或被丢弃的概率。

### 3. 手动同步
```powershell
python sync-forks.py
python sync-forks.py repo-name
python sync-forks.py --dry-run
python sync-forks.py --json
```

### 4. 查看状态
```powershell
python list-forks.py
python list-forks.py --json
python sync-forks.py --json --verify-schedule
python sync-forks.py --json --verify-releases
```

`--verify-schedule` 是只读检查，用于确认 fork 上的同步 workflow、`SYNC_PAT` secret 和最近一次定时运行是否满足自动同步条件。JSON 输出会包含：

- `workflow_installed`：目标 fork 是否存在 `.github/workflows/sync-fork.yml`
- `workflow_current` / `workflow_update_reason`：现有 workflow 是否匹配当前安全模板；缺少 `workflow_dispatch` 或模板漂移时会建议 `update_sync_workflow`
- `secret_sync_pat_present`：目标 fork 是否存在 `SYNC_PAT`
- `last_scheduled_run`：最近一次 schedule run 的状态
- `last_smoke_run`：没有 schedule 历史时，最近一次手动 `workflow_dispatch` smoke run 的状态
- `next_action`：下一步建议，例如 `none`、`install_sync_workflow`、`update_sync_workflow`、`set_sync_pat_secret`

非 JSON 模式会输出每个 fork 的 readiness 明细表，便于直接查看 `next_action`。

### 5. 稳定版源码策略
默认源码同步仍跟随上游默认分支；只有上游 release 带有实际安装/运行资产，且 fork 的新版本可能影响本机使用时，才进入 stable-source-only。

当前策略：

| fork | 源码同步策略 | 原因 |
|------|--------------|------|
| `sciman-top/cockpit-tools-local` | `stable_release_only` | 上游稳定版 release 带 `.exe`、`.msi`、`.deb`、`.rpm`、`.AppImage`、`.dmg`、签名和校验文件；默认分支自动合并可能把本机正在使用的安装版本带到未验证状态 |
| 其他 fork | `upstream_default_branch` | 当前 release 无安装资产，源码跟随默认分支更适合获取修复 |

stable-source-only 仓库在本地 `sync-forks.py` 和生成的 `sync-fork.yml` 中都会跳过默认分支 `merge-upstream`，因此不会创建或刷新 `backup/pre-sync`。`sciman-top/cockpit-tools-local` 的远端分支只保留 `main` 和 `mirror/upstream-main`，不长期保留 `backup/*` 分支；release 改为自用版构建产物，不再镜像官方安装资产。其他未来需要安装包镜像的 fork 仍可使用通用 release 镜像检查。

统一恢复点规则：`upstream_default_branch` 仓库保留一个移动的 `backup/pre-sync`，用于真实默认分支同步回滚；`stable_release_only`、self-use 或 mirror-only 仓库不长期保留 `backup/*`，只记录镜像分支旧 SHA、新 SHA 和回滚命令。

### 5.1 Cockpit Tools 自用分支更新
`sciman-top/cockpit-tools-local` 的默认分支 `main` 是自用源码主线；定时任务不会自动合并上游源码，策略是“保留自用特性，把它们重放到官方最新版上”。固定入口：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/Update-CockpitSelfUseFork.ps1 -PlanOnly
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/Update-CockpitSelfUseFork.ps1 -RunBuild
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/Update-CockpitSelfUseFork.ps1 -RunBuild -Push
```

脚本默认操作 `D:\CODE\external\Cockpit-Tools-Local` 的本地 `main`，其中 `origin` 指向 `sciman-top/cockpit-tools-local`，`upstream` 指向官方 `jlcodes99/cockpit-tools`。更新时从官方 `upstream/main` rebase，创建本地 `backup/selfuse-pre-rebase-<timestamp>` 备份分支。遇到冲突会停止，不会自动覆盖；冲突处理原则是保留明确的自用能力（例如 Cockpit API Service、单号池、投影链和本机快捷入口修复），同时接入官方兼容的新功能、修复和依赖更新。确认后使用 `--force-with-lease` 推送到 `origin/main`，避免无意覆盖远端新提交。

`origin/mirror/upstream-main` 是 fork 内的官方最新版源码镜像。它是可移动镜像分支，后续不再为上游 `main` 额外保留 `backup/upstream-main-<timestamp>` 或其他 `backup/*` 固定恢复点；刷新镜像前后必须在 `docs/change-evidence/` 记录旧 SHA、新 SHA 和回滚方式。不要把镜像分支当作自用开发入口。

### 6. Release 策略
`sciman-top/cockpit-tools-local` 使用 `self_use_local_releases`：

- Releases 只发布 Cockpit Tools Local 自用版构建产物。
- 官方稳定版安装包不再镜像到 `cockpit-tools-local`。
- 历史官方镜像 release 若仍带安装资产，`verify-releases` 会返回 `next_action=remove_upstream_mirror_release_assets`。

其他需要保留上游安装包的 fork 仍可使用 `latest_stable_assets_only`：

- 只把 `draft=false` 且 `prerelease=false` 视为稳定版。
- 最新稳定版保留安装/打包资产、签名、校验文件和 `latest.json`。
- 旧稳定版只保留 tag、release 标题和更新说明；不得保留 `.exe`、`.msi`、`.deb`、`.rpm`、`.AppImage`、`.dmg`、`.zip`、`.tar.gz` 等安装/打包资产。
- `latest.json` 若用于 fork 安装入口，必须指向 `sciman-top/...` 的 fork release 下载地址，并同步更新 `SHA256SUMS.txt` 中 `latest.json` 的 hash。

只读核验命令：

```powershell
python sync-forks.py --verify-releases --json
python sync-forks.py --verify-releases cockpit-tools-local --json
```

JSON 输出中 `next_action=prune_old_stable_release_assets` 表示旧稳定版仍带资产，需要删除旧资产但保留 release 文案；`next_action=mirror_latest_stable_release` 或 `mirror_latest_stable_assets` 表示最新稳定版还未完整保留安装/打包资产。

`--verify-releases` 是只读门禁，不会自动创建、上传或删除 release 资产。只要发现需要镜像最新稳定版、清理旧稳定版资产，或清理 `cockpit-tools-local` 上的历史官方镜像资产，命令会以 safety exit code `3` 退出；这不是脚本崩溃，而是阻断信号，表示需要执行一次中风险远端 release 维护后再复测。

通用稳定版 release 镜像更新闭环：

1. 先运行 `python sync-forks.py --verify-releases --json` 确认具体 `next_action`。
2. 若需要镜像最新稳定版，先从 upstream 下载稳定 release 资产，再在 fork 创建同 tag release 并上传资产。
3. 若存在 `latest.json`，必须把下载 URL 改写到 `sciman-top/...` fork release，并同步更新 `SHA256SUMS.txt` 里 `latest.json` 的 hash。
4. 若旧稳定版仍保留安装/打包资产，只删除旧资产，保留 tag、release 标题和说明。
5. 最后重跑 `python sync-forks.py --verify-releases --json`，确认 `ready == total` 且退出码为 `0`。

## 配置与认证
### 认证方式
本项目支持两种认证方式，优先级如下：

1. 环境变量令牌：`GH_TOKEN`，其次 `GITHUB_TOKEN`
2. `gh auth login` 的本地登录配置

推荐策略：

- 自动化/CI/临时会话：优先使用环境变量令牌
- 本地长期使用：可以继续使用 `gh auth login`

环境变量模式示例：

```powershell
$env:GH_TOKEN = "ghp_xxx"
python sync-forks.py --json
```

`gh auth login` 模式的本地凭据位置：

- Windows: `%APPDATA%\GitHub CLI\hosts.yml`
- Linux/macOS: `~/.config/gh/hosts.yml`

本仓库的 CI 使用 GitHub Actions 默认令牌；生成到 fork 仓库的同步 workflow 使用 `${{ secrets.SYNC_PAT }}`，不会把令牌写进代码。
如果使用环境变量令牌或 `SYNC_PAT` 来修改 workflow，请确保令牌具备相应仓库和 workflow 权限。
fork 发现会使用已认证账号的仓库视图；私有 fork 是否可见取决于令牌或 `gh auth login` 的仓库访问权限。

### Codex / PowerShell 环境诊断
如果在普通终端里 `gh auth status` 正常，但在 Codex shell 或自动化进程里失败，先运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1
```

该诊断会先 dot-source `scripts/Initialize-WindowsProcessEnvironment.ps1`，补齐当前进程缺失的 Windows 基础变量和安全白名单代理变量，再检查：

- 必需环境变量是否存在
- `SystemRoot`、`APPDATA`、`LOCALAPPDATA` 路径是否可读
- `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 等代理变量是否已进入当前进程（如用户环境或 Codex config 已配置）
- `gh` 是否在 `PATH` 中
- `gh auth status` 是否可用
- Python 是否能解析 `api.github.com`
- Python 是否具备工作区写入/删除能力
- `python -m py_compile gh_utils.py` 是否可执行（用于识别 `.pyc` 原子写入权限阻塞）

如果只想检查环境变量和 PATH，不访问 GitHub 认证状态：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1 -SkipGh
```

如果只想跳过 Python 写入与 `py_compile` 探针（例如只做网络/认证排查）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1 -SkipPythonWriteProbe
```

Python 入口也会在导入 `gh_utils.py` 时自动补齐同一组 Windows 基础变量和安全白名单代理变量，所以直接执行 `python sync-forks.py --json` 或 `python list-forks.py --json` 时也具备同样的防护。

### 依赖
- Python 3.8+
- GitHub CLI (`gh`)
- Windows 10/11

## 文件结构
```text
github-toolkit/
├── setup.bat          环境配置入口
├── sync-forks.py      同步与 workflow 管理核心
├── list-forks.py      状态查看
├── gh_utils.py        GitHub API / CLI 辅助函数
├── scripts/           诊断、基准与辅助脚本
└── README.md          本文件
```

## 常用命令速查
```powershell
python sync-forks.py                  # 立即同步所有 fork
python sync-forks.py --add-workflow   # 添加或更新同步 workflow
python sync-forks.py --verify-schedule --json  # 只读检查定时同步 readiness
python sync-forks.py --verify-releases --json  # 只读检查 release 资产保留策略
python sync-forks.py --dry-run        # 预览模式
python sync-forks.py --json           # 输出 JSON 摘要
python sync-forks.py --force          # 跳过安全检查（需设置 SYNC_FORKS_FORCE_ACK）
python sync-forks.py repo-name        # 只操作指定仓库
python list-forks.py                  # 查看 fork 列表和状态
python list-forks.py --json           # 输出 JSON 状态
```

## JSON 错误码（error_code）
`--json` 模式下，错误响应会包含稳定的 `error_code` 字段，便于自动化脚本分流：

| error_code | 场景 |
|------------|------|
| `E_CLI_UNKNOWN_ARGUMENT` | `list-forks.py` 参数错误 |
| `E_CLI_UNKNOWN_OPTION` | `sync-forks.py` 参数错误 |
| `E_FORCE_CONFIRMATION_REQUIRED` | 使用 `--force` 执行真实写操作时缺少显式确认 |
| `E_AUTH_CONTEXT_FAILED` | 认证存在问题（令牌无效/未登录/权限不足等） |
| `E_FORK_DISCOVERY_FAILED` | 拉取 fork 列表失败 |
| `E_GH_TIMEOUT` | GitHub API 调用超时 |
| `E_GH_RATE_LIMIT` | GitHub API 限流 |
| `E_GH_AUTH` | GitHub API 鉴权失败 |
| `E_GH_NOT_FOUND` | GitHub API 资源不存在 |
| `E_GH_INVALID_RESPONSE` | GitHub API 返回非 JSON 或异常响应 |

其余底层错误码会沿用 `E_GH_*` 统一命名。

## 集成验证（真实 GitHub API）
仓库提供独立 workflow：`.github/workflows/integration-api-smoke.yml`，用于在真实 GitHub API 下执行只读/干跑验证。

### 需要配置的仓库 Secret
- `INTEGRATION_GH_TOKEN`：集成验证专用 token（建议最小权限）
- `INTEGRATION_EXPECTED_LOGIN`：期望登录名（用于防误用 token）
- `INTEGRATION_TARGET_REPO`（可选）：指定一个 fork 目标用于 repo 级 dry-run 校验

### 运行方式
- 手动触发 `Integration API Smoke`（可选输入 `target_repo`）
- 或按周定时自动触发（UTC 每周一 03:30）

该 workflow 默认执行：
- `list-forks.py --json`
- `sync-forks.py --json --dry-run`
- （可选）指定仓库的 `--dry-run` 与 `--add-workflow --dry-run`

如果未配置 `INTEGRATION_GH_TOKEN` 或 `INTEGRATION_EXPECTED_LOGIN`，workflow 会成功退出并记录 skip 原因，不会把缺少集成测试密钥误报为代码失败。

## 性能基准（热路径）
提供本地可重复的无网络基准脚本：

```powershell
python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check
```

该基准覆盖 fork 状态收集、fork discovery hydration、全量 sync report、指定仓库去重 sync report，以及 `--verify-schedule` readiness 行构建。

可选输出到文件：

```powershell
python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check --output docs/benchmark-latest.json
```

## 注意事项
- 如果上游仓库发生删除或大规模清空，默认会拒绝同步
- 如果需要调试同步流程，优先使用 `--dry-run`
- 如果要接入外部脚本，优先使用 `--json`
- 如果 `gh` 在 Codex shell 中报未登录，先跑 `scripts/Verify-WindowsProcessEnvironment.ps1`
- 不要把本地认证文件提交到仓库
- 不要把 `.env`、令牌文件或私钥提交到仓库
