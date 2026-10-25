# GitHub Toolkit

<p align="center">
  <b>🛡️ 生产级 GitHub Fork 安全维护与 Release 资产自动化同步套件</b><br>
  <i>零第三方依赖 · 纵深防御安全熔断 · 自动创建恢复点 · Release 资产镜像与 latest.json 智能改写</i>
</p>

<p align="center">
  <a href="https://github.com/sciman-top/github-toolkit/actions/workflows/ci.yml"><img src="https://github.com/sciman-top/github-toolkit/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/gh%20cli-official-2088FF.svg?logo=github" alt="GitHub CLI">
  <img src="https://img.shields.io/badge/tests-58%20passed-success.svg" alt="Unit Tests">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <a href="README.en.md">English</a>
</p>

---

## 为什么需要 GitHub Toolkit？

在日常开发与企业基础设施维护中，我们经常需要 Fork 开源项目用于内部定制、安全加固、定制构建或提供网络镜像。然而维护数十个 Fork 往往面临重重痛点：

- **GitHub 原生网页同步（Sync Fork）隐患极大**：官方 Web UI 是盲目合并。若上游仓库遭遇恶意清空（Wipe）、恶意推送到默认分支、意外误删文件（如删除了 `.github/`、`README` 或 `LICENSE`），原生同步会直接将灾难同步至你的 Fork，毁掉所有积累。
- **缺乏版本恢复保障**：原生合并直接覆盖默认分支，一旦出问题只能在 reflog 中大海捞针。
- **Release 二进制资产完全不同步**：Git 只同步代码提交，不会同步 Releases。上游发布了最新的 `.exe`、`.dmg`、`.tar.gz` 等安装包，你的 Fork Release 依然空空如也；客户端自动更新依赖的 `latest.json` 依然硬编码指向外部上游地址。
- **第三方 Marketplace Actions 供应链风险高**：市面上的 fork-sync Action 多由个人维护，需要授予敏感的写权限 PAT，存在严重的凭据外泄与供应链投毒风险。

**GitHub Toolkit** 专为解决这些问题而设计：基于 100% Python 标准库与官方 GitHub CLI 构建，提供多层防御性安全熔断、全批次无写入 Preflight 预演、自动备份恢复点、Release 资产同步镜像以及 `latest.json` URL 改写与哈希清单联动更新。

---

## 核心特性对比

| 功能特性 | GitHub 网页 Sync Fork | 常见第三方 Action | GitHub Toolkit |
| :--- | :---: | :---: | :---: |
| **多仓批量发现与状态一览** | ❌ 仅能逐个点击 | ❌ 需逐仓配置 | ✅ **自动并发发现全账号 Fork** |
| **清空与删库熔断保护** | ❌ 盲目合并 | ❌ 无保护 | ✅ **文件数突降 50% 立即熔断** |
| **大批量误删保护** | ❌ 无限制 | ❌ 无限制 | ✅ **动态删除预算上限拦截** |
| **关键路径误删保护** | ❌ 无拦截 | ❌ 无拦截 | ✅ **保护 `.github/`、`README` 等** |
| **API 截断逃逸拦截** | ❌ 无校验 | ❌ 无校验 | ✅ **Compare 达 300 条自动拦截** |
| **同步前自动创建恢复点** | ❌ 无备份 | ❌ 无备份 | ✅ **写入前更新 `backup/pre-sync`** |
| **全批次 Preflight 预演** | ❌ 不支持 | ❌ 不支持 | ✅ **任一目标异常即整批中止** |
| **Release 二进制镜像同步** | ❌ 不支持 | ❌ 不支持 | ✅ **同步安装包、签名与校验文件** |
| **`latest.json` 自动更新改写** | ❌ 不支持 | ❌ 不支持 | ✅ **改写下载 URL 为 Fork 地址** |
| **校验清单联动重算** | ❌ 不支持 | ❌ 不支持 | ✅ **自动更新 SHA256/512 并原子上传** |
| **旧镜像资产安全清理** | ❌ 不支持 | ❌ 不支持 | ✅ **仅清理已证镜像，保留自定义资产** |
| **依赖安全性** | 官方服务 | 依赖第三方镜像/脚本 | ✅ **100% Python 标准库 + 官方 `gh`** |

---

## 安全同步流水线架构

每一次默认分支同步均严格遵循六重防御性流水线：

```text
 ┌────────────────────────────────────────────────────────┐
 │ 1. 运行时动态发现 (Fresh Discovery)                    │
 │    通过 GitHub API 实时获取 Fork 与上游默认分支与元数据 │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 2. 全批次 Preflight 预演检查                           │
 │    在产生任何写操作前，对目标仓库完成无写入安全比对    │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 3. 纵深防御安全熔断 (Safety Guardrails)                │
 │    • 上游文件数 < Fork 50% ➔ 熔断中断                  │
 │    • 误删文件数 > 动态配额 (5 + Fork/10) ➔ 熔断中断   │
 │    • 删除 .github/、README、LICENSE 等关键路径 ➔ 熔断  │
 │    • GitHub Compare 差异达 300 条疑似截断 ➔ 熔断       │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 4. 自动创建移动恢复点 (Restore Point)                  │
 │    在真实 Merge 之前，自动更新分支 backup/pre-sync     │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 5. 上游合并与收敛复核 (Merge & Convergence Verification)│
 │    调用 merge-upstream，再次比对确保 behind_by == 0    │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 6. Release 资产镜像与清理 (Release Convergence)        │
 │    拉取新安装包、改写 latest.json、重算摘要、清理旧镜像 │
 └────────────────────────────────────────────────────────┘
```

---

## 30 秒快速上手

### 1. 环境准备

本工具仅依赖 Python 3.11+ 和官方 [GitHub CLI (`gh`)](https://cli.github.com/)：

```powershell
# Windows 用户可通过 winget 一键安装 GitHub CLI
winget install --id GitHub.cli --source winget

# 登录 GitHub 账号（完成本地认证）
gh auth login
```

> **凭据优先级**：自动化环境优先读取环境变量 `GH_TOKEN`，其次 `GITHUB_TOKEN`；本地终端自动复用 `gh auth login` 凭据。工具全程不读取、不打印、不持久化明文密码。

### 2. 核心操作命令

#### ① 查看当前所有 Fork 状态（只读）

```powershell
# 打印格式化终端表格
python list-forks.py

# 获取结构化 JSON（供脚本集成或监控接入）
python list-forks.py --json
```

终端将清晰展示仓库名、分支、上游源、落后提交数（Behind）、以及同步 Workflow 安装状态：

```text
Fork repos for your-name (3 total):

Discovery: live_github_api /user/repos at 2026-10-25T12:00:00Z (repos=12, forks=3)

Repository                          Branch     Upstream                            Behind   Workflow
-------------------------------------------------------------------------------------------------------
my-awesome-fork                     main       upstream-org/my-awesome-fork        2        Installed
devtools-mirror                     master     original/devtools-mirror            0        Missing
cockpit-custom                      main       cockpit-project/cockpit             Unknown  Installed
```

#### ② 模拟预览（Dry-Run）

在真实写入前，强烈建议先执行 `--dry-run` 预览变更：

```powershell
# 模拟全账号全部 Fork 同步
python sync-forks.py --dry-run

# 模拟单个仓库同步
python sync-forks.py my-awesome-fork --dry-run
```

#### ③ 执行安全同步

```powershell
# 同步全部 Fork
python sync-forks.py

# 同步指定 Fork（支持单个或多个，传 name 或 owner/name）
python sync-forks.py my-awesome-fork other-owner/another-fork
```

#### ④ 批量安装 / 更新自动同步工作流

为所有 Fork 批量部署或无损升级受控的 `sync-fork.yml`（每周定时运行且支持手动触发）：

```powershell
# 先预览待安装/待更新列表
python sync-forks.py --add-workflow --dry-run

# 真实部署
python sync-forks.py --add-workflow
```

#### ⑤ 巡检定时工作流与 Secret 健康度

```powershell
python sync-forks.py --verify-schedule
```

#### ⑥ Release 资产镜像与清理

```powershell
# 仅核对资产差异（只读）
python sync-forks.py --verify-releases

# 模拟执行资产收敛
python sync-forks.py --sync-releases --dry-run

# 真实执行资产收敛
python sync-forks.py --sync-releases my-awesome-fork
```

---

## 模块与命令全量参考

### 1. `list-forks.py`

| 参数 | 说明 |
| :--- | :--- |
| *(无参数)* | 终端表格输出全账号 Fork 状态、落后提交数及 Workflow 安装状态 |
| `--json` | 输出机器可读的结构化 JSON，包含认证身份、Discovery 证据与逐仓明细 |
| `-h, --help` | 显示帮助信息 |

> **状态严谨性保障**：若因 API 限制或权限问题无法确认状态，脚本明确返回 `Unknown` 并附带诊断原因，绝不将未知状态伪装为 `Missing`。

### 2. `sync-forks.py`

| 参数 / 选项 | 模式分类 | 功能说明 |
| :--- | :---: | :--- |
| `[repo-name ...]` | 目标选择 | 指定一个或多个仓库（`repo` 或 `owner/repo`），自动去重并保持顺序；省略则处理全部 Fork |
| `--dry-run` | 模拟执行 | 完整运行真实 API 读取与安全比对规则，不产生任何 GitHub 写入 |
| `--add-workflow` | 工作流分发 | 在目标 Fork 中创建或更新 `.github/workflows/sync-fork.yml` |
| `--verify-schedule` | 只读审计 | 巡检各 Fork 的 Workflow 安装状态、`SYNC_PAT` 密钥配置及最近定时运行历史 |
| `--verify-releases` | 只读审计 | 对比上游与 Fork 最新稳定版资产差异与保留策略 |
| `--sync-releases` | 资产收敛 | 镜像最新稳定版安装包、改写 `latest.json` 并清理旧版本上游镜像 |
| `--force` | 强制模式 | 绕过部分安全限制或强制重写工作流模板（真实写入需二次确认） |
| `--json` | 输出控制 | 打印包含运行摘要、前置检查结果与逐仓操作明细的 JSON |
| `-h, --help` | 帮助信息 | 打印完整的命令行使用说明 |

### 3. 强制写入二次确认机制（`--force`）

为了防止自动化流水线或手误造成不可逆破坏，当使用 `--force` 且涉及真实远端写入时，必须显式配置环境变量确认：

```powershell
# PowerShell
$env:SYNC_FORKS_FORCE_ACK = "I_UNDERSTAND"
python sync-forks.py --force

# Windows CMD
set SYNC_FORKS_FORCE_ACK=I_UNDERSTAND
python sync-forks.py --force

# Linux / macOS Bash
SYNC_FORKS_FORCE_ACK=I_UNDERSTAND python3 sync-forks.py --force
```

### 4. 退出码契约（Exit Codes）

脚本在 CLI 中严格遵循以下退出码规范，方便 CI/CD 流水线集成：

- `0`：操作成功，或无需进行任何写入动作。
- `1`：CLI 缺失、凭据无效、GitHub API 网络错误，或参数语法解析错误；若 `list-forks.py` 存在任一仓状态未能成功确认，也返回 1。
- `3`：安全防护规则触发熔断阻断，或只读巡检发现存在需要人工介入/处理的待办项。

---

## Release 资产同步与智能改写详解

许多现代开源桌面应用（如 Tauri、Electron 构建的客户端）依赖 GitHub Releases 进行分发与自动更新。普通的 Git Fork 只能同步源码分支，无法镜像 Release。

`--sync-releases` 针对这一场景提供了端到端的镜像解决方案：

```text
上游 Release (v1.2.0)                          Fork Release (v1.2.0)
 ├── app-setup.exe         ─── 同步镜像 ───►    ├── app-setup.exe
 ├── app-setup.dmg         ─── 同步镜像 ───►    ├── app-setup.dmg
 ├── latest.json           ─── 智能改写 ───►    ├── latest.json (URL 改写为 Fork 链接)
 └── SHA256SUMS.txt        ─── 联动更新 ───►    └── SHA256SUMS.txt (重新计算校验值)
```

1. **白名单资产精准匹配**：识别 `.exe`、`.msi`、`.deb`、`.rpm`、`.appimage`、`.dmg`、`.pkg`、`.zip`、`.tar.gz` 等安装包，`.sig`、`.minisig` 签名，以及哈希清单与 `latest.json`。
2. **`latest.json` 自动改写**：解析 JSON 内容，自动将下载地址从 upstream 域名前缀改写为当前 fork 仓库的下载地址。
3. **校验清单联动重算**：若包内含 `SHA256SUMS.txt` 或 `SHA512SUMS.txt`，自动计算新 `latest.json` 的最新哈希并替换对应行。
4. **元数据原子上传**：将 `latest.json` 与校验清单作为同一批次元数据 bundle 上传，避免因单文件上传时差造成校验失败。
5. **资产名称安全清洗**：严禁包含路径分隔符（`/`、`\`）、NUL 字符、父目录逃逸（`..`）或 CLI 参数前缀（`-`）的异常文件名。
6. **已证明镜像清理策略**：当上游发布更高版本时，仅清理通过“同 tag、同名且相同 digest/size”严密证明来自上游的旧镜像资产；**绝对不会删除用户在 Fork 上自行上传的个性化资产或自定义 Tag**。

---

## GitHub Actions 自动化运维

本项目自带三套互补的 GitHub Actions 工作流，可直接接入持续交付流程：

- **`ci.yml`**：在代码提交或 PR 时自动运行全部 58 项单元测试。
- **`auto-setup-forks.yml`**：每周日定时自动扫描全账号；若发现了新创建的 Fork，或 `sync-forks.py` 模板发生升级，将自动为所有 Fork 安装/更新最新的 `sync-fork.yml` 同步流水线。
- **`integration-api-smoke.yml`**：每周一使用只读凭据针对真实 GitHub API 进行冒烟测试，巡检接口兼容性与结构体变化。

### 仓库 Secret 配置

在运行自动化运维流水线时，按需配置以下 GitHub Actions Secrets：

| Secret 名称 | 必须 | 作用与权限说明 |
| :--- | :---: | :--- |
| `SYNC_PAT` | 是 | 具备 `repo` 与 `workflow` 权限的 Personal Access Token，用于向各 Fork 提交工作流文件及执行跨仓同步 |
| `INTEGRATION_GH_TOKEN` | 否 | 用于 API 冒烟测试的专用只读 Token |
| `INTEGRATION_EXPECTED_LOGIN` | 否 | 用于冒烟测试校验 Token 所属账号主体，防止凭据错配 |
| `INTEGRATION_TARGET_REPO` | 否 | 冒烟测试时可选针对的指定测试 Fork 仓库全名 |

---

## 灾难恢复指南

如果上游仓库发生重大破坏且未被防护规则拦截（或使用了 `--force`），您可以借助自动创建的恢复分支进行一键秒级回退：

```bash
# 1. 切换到本地 Fork 仓库目录
cd path/to/your-fork

# 2. 从远端拉取自动生成的恢复分支
git fetch origin backup/pre-sync

# 3. 检查备份点的状态与提交记录
git log -n 5 origin/backup/pre-sync

# 4. 将默认分支硬重置到同步前的安全状态
git checkout main
git reset --hard origin/backup/pre-sync

# 5. 强制推送到 Fork 远端完成恢复
git push origin main --force
```

---

## 本地开发与测试

运行完整的单元测试套件（无外部依赖，耗时通常 < 0.1 秒）：

```powershell
python -m unittest -q test_toolkit.py
```

测试集涵盖：
- GitHub CLI 探测与超时异常分类
- 凭证解析与并发上游父仓发现
- 工作流模板渲染与无损更新比对
- 50% 文件骤降、大批量删除与关键文件删除熔断拦截
- Compare 300 条 API 截断防护与恢复点维护
- 强制写入确认环境变量拦截
- Release 资产白名单过滤、URL 改写与 SHA256 联动重算
- 来源证明镜像比对与安全清理边界

---

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。欢迎提交 Issue 或 Pull Request 共同完善！
