# GitHub Toolkit

Fork 仓库自动同步管理工具集，带安全防护机制。

## 功能

| 脚本 | 用途 |
|------|------|
| `setup.bat` | 一键检测/安装 gh CLI、登录认证、添加 workflow 权限 |
| `sync-forks.py` | 核心：添加同步 workflow / 立即同步 fork（含安全检查） |
| `list-forks.py` | 列出所有 fork 仓库及同步状态 |

## 安全机制

同步前自动进行安全检查，防止上游仓库被删库清空后波及 fork：

- 比较上游与 fork 的文件数量
- 如果上游文件数低于 fork 的 50%，**自动中止同步**
- GitHub Actions 定时同步也内置同样的安全检查
- 可用 `--force` 强制跳过（不推荐）

## 快速开始

### 1. 首次使用

双击运行 `setup.bat`，自动完成：
- 检测/安装 GitHub CLI
- 浏览器授权登录
- 添加 workflow 文件操作权限

### 2. 给 fork 仓库添加自动同步

```powershell
# 自动扫描所有 fork 并添加同步 workflow
python sync-forks.py --add-workflow

# 只给特定仓库添加
python sync-forks.py --add-workflow repo-name

# 预览（不实际修改）
python sync-forks.py --add-workflow --dry-run
```

添加后，GitHub Actions 会在**每周日 UTC 02:00（北京时间 10:00）**自动同步。
也可在仓库的 Actions 页面手动触发。

### 3. 手动同步（立即执行）

```powershell
# 同步所有 fork
python sync-forks.py

# 同步特定仓库
python sync-forks.py repo-name

# 预览
python sync-forks.py --dry-run
```

### 4. 查看状态

```powershell
python list-forks.py
```

## 以后新增 fork 怎么办

Fork 新仓库后，执行：

```powershell
python sync-forks.py --add-workflow
```

脚本会自动检测新增的 fork（已有 workflow 的会跳过）。

## 命令速查

```
python sync-forks.py                  # 立即同步所有 fork
python sync-forks.py --add-workflow   # 添加/更新同步 workflow
python sync-forks.py --dry-run        # 预览模式
python sync-forks.py --force          # 跳过安全检查（危险）
python sync-forks.py repo-name        # 只操作指定仓库
python list-forks.py                  # 查看 fork 列表和状态
```

## 文件说明

```
github-toolkit/
├── setup.bat          环境配置（gh CLI 安装、认证）
├── sync-forks.py      核心脚本（同步 + workflow 管理）
├── list-forks.py      状态查看
└── README.md          本文件
```

## 认证方式（当前）

> **本项目暂时使用明文方式存储凭据。**

- 通过 `gh auth login` 以 HTTPS 协议认证，OAuth token 以**明文**保存在本地配置文件中：
  - Windows: `%APPDATA%\GitHub CLI\hosts.yml`
  - Linux/macOS: `~/.config/gh/hosts.yml`
- GitHub Actions 工作流使用 `${{ secrets.GITHUB_TOKEN }}`，该令牌由 GitHub 运行时自动注入，不在代码中硬编码。
- **后续计划**：迁移到 SSH 密钥 或 `gh` 内置 keyring 集成，实现加密凭据存储。

## 依赖

- Python 3.8+
- GitHub CLI (`gh`) — setup.bat 会自动安装
- Windows 10/11
