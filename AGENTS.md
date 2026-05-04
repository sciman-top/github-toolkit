# AGENTS.md — github-toolkit（共同项目规则 / Codex 直接读取）
**项目**: github-toolkit
**承接来源**: `GlobalUser/AGENTS.md v9.52`
**适用范围**: 项目级（仓库根）
**最后更新**: 2026-05-04

## 1. 阅读指引
- 本文件只写本仓事实、门禁命令、证据位置和回滚入口，不重写全局 `R/E` 语义。
- 本文件是三工具共同项目规则主体；Codex 直接读取，Claude/Gemini 通过各自 wrapper 的 `AGENTS.md` import 承接并只追加平台差异。
- 固定结构：本文件保持 `1 / A / B / C / D`；Claude/Gemini wrapper 保持 `1 / B / D`，并通过 import 承接本文件 `A/C/D`。
- 裁决链：`运行事实/代码 > 项目级文件 > 全局文件 > 临时上下文`。
- 自包含约束：执行规则以本文件正文为准，不依赖外部子文档或治理脚本作为前置条件。
- 渐进披露边界：根文件保留本仓边界、门禁、阻断、证据和回滚；长 GitHub API 排障、批量同步样例和历史证据放入 `docs/`。

## A. 项目基线
### A.1 事实边界
- 本仓是 Python GitHub fork/tooling 辅助仓，核心脚本为 `gh_utils.py`、`sync-forks.py`、`list-forks.py`、`test_toolkit.py`、`test_support.py`。
- 真实同步必须保留 `--dry-run` / 显式确认边界；`--force` 需要 `SYNC_FORKS_FORCE_ACK=I_UNDERSTAND`。
- 同步保护包括 upstream 可达性、文件数量阈值、关键路径删除保护和 `backup/pre-sync` 恢复点。
- `GH_TOKEN` / `GITHUB_TOKEN` / `gh auth login` 是认证输入；不得把真实 token 写入仓库文件、日志或证据正文。

### A.2 执行锚点
- 每次改动先声明：当前落点 -> 目标归宿 -> 验证方式。
- 默认中文沟通、中文解释、中文汇报；代码标识符、命令、日志、报错和 GitHub API 字段保留英文原文。
- 全局规则给风险、语言、N/A 和门禁语义；本文件给 github-toolkit 的脚本边界、真实命令、删除保护、证据与回滚入口。
- 项目规则只保留本仓不可由代码/CI自动推断且会改变执行、风险或验收的事实；长流程下沉到子文档或工具专属规则。
- 规则文件、门禁、profile、baseline 或同步脚本修改前，必须先比对控制仓 `governed-ai-coding-runtime/rules/manifest.json`、源文件、用户目录/目标仓已分发副本、目标仓真实 gate/profile/CI/script/README 差异和当前工具官方加载模型；发现漂移先整合再同步，不盲目覆盖。
- 小步闭环，优先根因修复；止血补丁必须标明回收时点。

### A.3 N/A 分类与字段
- `platform_na`：平台能力缺失、命令不存在、非交互限制或认证上下文不可用。
- `gate_na`：门禁步骤客观不可执行（含脚本缺失、GitHub 认证不可用、纯文档/注释/排版改动）。
- 两类 N/A 均必须记录：`reason`、`alternative_verification`、`evidence_link`、`expires_at`。
- N/A 不得改变门禁顺序：`build -> test -> contract/invariant -> hotspot`。

### A.4 触发式澄清协议
- 默认执行：`direct_fix`（先修复、后验证）。
- 触发条件：同一 `issue_id` 连续失败达到阈值（默认 `2`），或同步风险/用户期望持续冲突。
- 一次最多 3 个澄清问题；确认后恢复 `direct_fix` 并清零失败计数。
- 留痕字段：`issue_id`、`attempt_count`、`clarification_mode`、`clarification_questions`、`clarification_answers`。

## B. Codex 平台差异
- 用户目录：`~/.codex`（可由 `CODEX_HOME` 覆盖）。
- 项目链从 Git root 到当前目录逐层加载；同层优先级：`AGENTS.override.md > AGENTS.md > configured fallback`。
- `AGENTS.override.md` 仅用于短期排障，结论后必须清理并复测。
- 诊断优先执行 `codex --version`、`codex --help`；`codex status` 非交互失败时按 `platform_na` 记录。
- `AGENTS.md` 是上下文规则；确定性验证、权限或安全拦截应落到本仓门禁、hooks、CI 或管理脚本。
- 命令审批、沙箱外执行和 allowlist 应优先写入 `.codex/rules/*.rules`、本仓脚本门禁或 CI；`prefix_rule()` 必须保持精确前缀并配 `match/not_match` 样例。
- 替代命令仅用于补证据，不得改变本仓门禁顺序与阻断语义。

## C. 项目差异（领域与技术）
### C.1 模块职责
- `gh_utils.py`：GitHub CLI/API、认证、同步保护与 Windows 环境补齐。
- `sync-forks.py`：workflow 安装、手动同步、dry-run、force 确认与 JSON 输出。
- `list-forks.py`：fork 枚举与同步状态输出。
- `test_toolkit.py`、`test_support.py`：回归测试与受限环境 mock fallback。
- `scripts/Verify-WindowsProcessEnvironment.ps1`：Codex/PowerShell/`gh` 环境诊断。
- `scripts/benchmark_hotpaths.py`：fork 列表/同步热点基准。
- `.claude/hooks/governed-pre-tool-use.py`：Claude Code 工具前置安全拦截（敏感文件读取、direct `powershell.exe` 调用）。

### C.2 门禁命令与顺序（硬门禁）
- build：`python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- test：`python -m unittest test_toolkit.py`
- contract/invariant：`python -m unittest test_toolkit.py`
- hotspot：复核本次改动的 GitHub API 参数、删除保护、dry-run/force 确认边界；触及性能路径时运行 `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`。
- fixed order：`build -> test -> contract/invariant -> hotspot`

### C.3 失败分流与阻断
- build 失败：阻断，先修语法、导入和脚本路径。
- test 失败：阻断，先修回归失败再继续。
- contract/invariant 失败：高风险阻断，禁止真实同步或发布。
- hotspot 未收敛：阻断；涉及真实 GitHub 写操作时必须先 dry-run 并记录恢复入口。

### C.4 证据与回滚
- 证据目录：`docs/change-evidence/`（不存在则创建）。
- 最低字段：规则 ID、风险等级、执行命令、关键输出、是否触发真实 GitHub 写操作、回滚动作。
- 回滚优先使用 git 历史；真实同步变更还必须记录 `backup/pre-sync` 或等价恢复点。

### C.5 CI 与本地入口
- 本地门禁以 C.2 命令为准。
- `setup.bat` 只负责本机 `gh` / 登录准备，不替代门禁。
- Codex/PowerShell 环境异常先运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1`。

### C.6 Git 提交与推送边界
- `整理提交全部` 的“全部”仅指：`本次任务相关 + 应被版本管理 + 通过 .gitignore 的文件`。
- 默认不纳入：真实 token、临时日志、缓存、运行态探针、本机 `gh` 配置和敏感输出。
- `push` 仅推送既有 commit 历史；文件筛选必须在 `git add/commit` 前完成。

## D. 维护校验清单
- 仅落地本仓事实，不复述全局规则正文。
- 与全局职责互补，不重叠、不缺失。
- 协同链完整：`规则 -> 落点 -> 命令 -> 证据 -> 回滚`。
- 协同有效性抽查：仅凭全局 + 项目规则，必须能推出本仓当前落点、目标归宿、硬门禁、证据路径和回滚入口。
- `Global Rule -> Repo Action`：
  - `R6`: 本仓门禁命令是硬门禁；quick/fast 只能作为已声明的日常反馈切片，交付前仍按 full gate 或固定顺序收口。
  - `R8`: 证据与回滚字段是最小留痕；缺字段必须按 N/A 口径说明。
  - `E4`: hotspot 复核 GitHub API 参数、删除保护、dry-run/force 确认和 `gh` 认证状态。
  - `E5`: Python、`gh`、workflow token 或第三方依赖变化必须记录供应链/工具基线；新增依赖前先说明必要性。
  - `E6`: GitHub API 字段、workflow YAML、同步状态 JSON 或持久配置结构变化必须记录兼容性和回滚。
- 本文件属于控制仓 `governed-ai-coding-runtime/rules/manifest.json` 管理的规则家族；目标仓现场修改必须回写控制仓源文件后再同步。
- 子文档只承载细节，不替代根文件中的硬门禁和项目事实。
- 三工具协同约束：`AGENTS.md` 承载共同 A/C/D 项目事实；`CLAUDE.md` / `GEMINI.md` 通过 import 追加 B/D 平台差异，不复制共同正文。
