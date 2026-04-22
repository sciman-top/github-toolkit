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
| `sync-forks.py` | 添加同步 workflow，或执行同步，支持 `--json` |
| `list-forks.py` | 查看所有 fork 和同步状态，支持 `--json` |
| `gh_utils.py` | 公共 GitHub 辅助逻辑 |

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

### 2. 给 fork 添加自动同步
```powershell
python sync-forks.py --add-workflow
python sync-forks.py --add-workflow repo-name
python sync-forks.py --add-workflow --dry-run
```

添加后，GitHub Actions 会在每周日 UTC 02:00，也就是北京时间 10:00 自动同步。

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
```

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

GitHub Actions 使用 `${{ secrets.GITHUB_TOKEN }}`，不会把令牌写进代码。
如果使用环境变量令牌来修改 workflow，请确保令牌具备相应仓库和 workflow 权限。

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
└── README.md          本文件
```

## 常用命令速查
```powershell
python sync-forks.py                  # 立即同步所有 fork
python sync-forks.py --add-workflow   # 添加或更新同步 workflow
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

## 性能基准（热路径）
提供本地可重复的无网络基准脚本：

```powershell
python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check
```

可选输出到文件：

```powershell
python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check --output docs/benchmark-latest.json
```

## 注意事项
- 如果上游仓库发生删除或大规模清空，默认会拒绝同步
- 如果需要调试同步流程，优先使用 `--dry-run`
- 如果要接入外部脚本，优先使用 `--json`
- 不要把本地认证文件提交到仓库
- 不要把 `.env`、令牌文件或私钥提交到仓库
