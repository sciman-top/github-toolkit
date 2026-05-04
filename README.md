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
- 每次同步前写入 `backup/pre-sync` 恢复点（手动同步与 GitHub Actions 一致）
- GitHub Actions 定时同步也会执行同样的检查

`--force` 默认需要显式确认环境变量后才能用于真实同步（`--dry-run` 不受此限制）：

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
```

`--verify-schedule` 是只读检查，用于确认 fork 上的同步 workflow、`SYNC_PAT` secret 和最近一次定时运行是否满足自动同步条件。JSON 输出会包含：

- `workflow_installed`：目标 fork 是否存在 `.github/workflows/sync-fork.yml`
- `workflow_current` / `workflow_update_reason`：现有 workflow 是否匹配当前安全模板；缺少 `workflow_dispatch` 或模板漂移时会建议 `update_sync_workflow`
- `secret_sync_pat_present`：目标 fork 是否存在 `SYNC_PAT`
- `last_scheduled_run`：最近一次 schedule run 的状态
- `next_action`：下一步建议，例如 `none`、`install_sync_workflow`、`update_sync_workflow`、`set_sync_pat_secret`

非 JSON 模式会输出每个 fork 的 readiness 明细表，便于直接查看 `next_action`。

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
| `E_FORCE_CONFIRMATION_REQUIRED` | 使用 `--force` 同步时缺少显式确认 |
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
