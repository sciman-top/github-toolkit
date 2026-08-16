# GitHub Toolkit

一个依赖 Python 标准库与 GitHub CLI 的 fork 维护工具。它只做三件事：

- `list-forks.py`：只读列出当前账号的 fork、上游差异和同步 workflow 状态；API 无法确认时明确报告 `Unknown`，不伪装成 `Missing`。
- `sync-forks.py`：dry-run 或同步 fork，并在真实默认分支写入前创建 `backup/pre-sync`。
- `sync-forks.py --verify-releases/--sync-releases`：核对或收敛最新稳定版资产。

仓库状态、默认分支、上游和认证均在运行时从 GitHub 获取；README 不保存易过期的成功快照。

## 准备

需要 Python 3.11+ 与 [GitHub CLI](https://cli.github.com/)。Windows 可运行：

```powershell
winget install --id GitHub.cli --source winget
gh auth login
```

自动化优先使用 `GH_TOKEN`，其次 `GITHUB_TOKEN`；本地交互可使用 `gh auth login`。脚本不会读取或保存明文密码。

## 常用命令

```powershell
# 只读状态
python list-forks.py
python list-forks.py --json

# 先预览，再同步
python sync-forks.py --dry-run
python sync-forks.py repo-name --dry-run
python sync-forks.py

# 安装或更新每个 fork 的 weekly sync-fork.yml
python sync-forks.py --add-workflow --dry-run
python sync-forks.py --add-workflow

# 稳定版资产只读核对与收敛
python sync-forks.py --verify-releases --json
python sync-forks.py --sync-releases --dry-run --json
python sync-forks.py --sync-releases repo-name
```

仓库参数可写 `name` 或 `owner/name`。多个仓库会去重并保持输入顺序。

## 安全语义

默认分支同步遵循固定顺序：

1. 从 GitHub API fresh discover fork 与上游默认分支。
2. 即使使用 `--dry-run`，也会真实读取并比较 fork/upstream 文件数量与删除内容；dry-run 本身不写 GitHub。
3. 上游文件数低于 fork 的 50%、删除文件数过大、compare 文件列表达到 300 条而可能截断，或删除 `.github/`、`README.md`、`LICENSE` 等关键路径时中止。
4. 真实同步前把当前 fork HEAD 写入移动恢复分支 `backup/pre-sync`。
5. 调用 GitHub `merge-upstream`，随后重新比较并确认 fork 不再落后；无法复核时按安全阻断返回，不把写入响应外推为已收敛。

`--dry-run` 不写 GitHub，但会执行必要的安全 API 读取。`--force` 会绕过部分安全检查，因此真实写入还必须显式确认：

```powershell
$env:SYNC_FORKS_FORCE_ACK = "I_UNDERSTAND"
python sync-forks.py --force
```

不要在日志、命令记录或仓库文件中打印 token。

## Release 收敛

当上游最新稳定版包含安装包、签名、校验文件或 `latest.json` 时，`--sync-releases` 可：

- 在 fork 创建或补齐同 tag 的稳定版资产；
- 把 `latest.json` 下载地址改写到 fork，并同步更新 `SHA256SUMS.txt`；
- 将 `latest.json` 与 SHA-256/SHA-512 清单作为一个元数据 bundle 覆盖上传，避免只缺其中一个文件时留下不匹配校验值；
- 拒绝包含路径分隔符、NUL 或 CLI 选项前缀的异常上游资产名，避免逃逸临时目录或改变参数解析；
- 删除旧稳定版的安装/校验资产，但保留 tag、标题和说明。

没有安装资产的上游按 `source_only_release_notes` 跳过。本地 release 子命令单次最长等待 10 分钟，生成的 workflow job 最长 30 分钟；执行器会在写入后重新核对，未收敛时返回 safety 状态，不把部分成功报告为完成。

## 输出与退出码

`--json` 输出包含认证来源、fresh discovery 证据、逐仓结果和 summary。稳定错误码用于区分 CLI、认证、网络/API 和发现失败。

- `0`：成功或无需动作。
- `1`：CLI、认证、API 或执行错误；`list-forks.py` 任一仓状态无法确认时也返回 1，并在 JSON 中保留逐仓错误。
- `3`：安全检查阻断，或只读核对发现需要人工/写入动作。

## GitHub Actions

- `ci.yml`：push/PR 只运行一套行为测试，不重复安装 lint/type/benchmark 工具。
- `auto-setup-forks.yml`：定时为新 fork 安装或更新同步 workflow；managed 文件与当前模板不完全一致时会收敛，避免保留未受管步骤。
- `integration-api-smoke.yml`：使用独立只读凭据运行最薄 GitHub API 主链；它是运营检查，不是每次本地编码门禁。

仓库 Secret：

- `SYNC_PAT`：安装 fork workflow，以及 fork workflow 自身同步所需权限。
- `INTEGRATION_GH_TOKEN`：integration smoke 的只读 token。
- `INTEGRATION_EXPECTED_LOGIN`：用于阻断 token 主体错配。
- `INTEGRATION_TARGET_REPO`：可选，只验证一个指定 fork 的参数路径。

## 开发验证

本地与 CI 使用同一条最低充分命令：

```powershell
python -m unittest -q test_toolkit.py
```

测试覆盖认证/API 错误、fork 发现、dry-run、删除保护、恢复点、force acknowledgement、JSON 错误和 release 安全结果。只有出现可复现的独立失败时才增加针对性验证；不维护逐任务证据文件、合成 benchmark 或重复 contract suite。
