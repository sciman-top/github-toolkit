# AGENTS.md - github-toolkit
**项目契约**: 2.0
**全局规则复核**: 9.73
**最后更新**: 2026-08-08

## 1. 当前落点与目标归宿
- 当前落点：本仓是 Python GitHub fork 与同步辅助工具，核心能力在 `gh_utils.py`、`sync-forks.py` 与 `list-forks.py`。
- 目标归宿：在明确 dry-run、删除保护、恢复点与认证边界下安全维护 fork 和 workflow，保持行为可审计、可回退。
- 下一最小里程碑：完成当前脚本切片，并以静态检查、回归、合同缺口说明与 hotspot 证据收口。
- 目标仓状态、默认分支、同步结果和线上验收均执行时 fresh query；根规则不保存仓列表、SHA 或成功快照。

## A. 仓库事实与模块边界
- `gh_utils.py`：GitHub CLI/API、认证与同步保护；`sync-forks.py`：workflow、同步、dry-run 和 force；`list-forks.py`：状态输出。
- `test_toolkit.py` 与 `test_support.py` 承载回归和受限环境 fallback；`scripts/benchmark_hotpaths.py` 承载合成热点基准。
- 真实同步必须先 dry-run；`--force` 需要 `SYNC_FORKS_FORCE_ACK=I_UNDERSTAND`。
- `upstream_default_branch` 仓保留移动恢复点 `backup/pre-sync`；stable-release 或 mirror-only 仓记录旧/新 SHA 与回滚命令。
- `GH_TOKEN`、`GITHUB_TOKEN` 与本机 `gh auth` 是认证输入，不得进入仓库、日志或证据正文。
- 真实主链是“发现仓型 -> dry-run 差异 -> 删除/关键路径保护 -> 建立恢复点 -> 授权 apply -> SHA/状态复核”；未形成恢复证据前不得写远端。

## B. 执行与风险边界
- setup/auth 不计门禁；认证缺失时完成无写入静态和测试证据，不得伪造线上状态。
- 真实默认分支、workflow 或 release 写操作属于中风险；执行前确认 dry-run、删除阈值、关键路径保护和恢复点。
- 新增依赖、API 字段或同步模式前说明必要性、向后兼容与 rollback；无证据不扩成通用平台。
- Windows 进程环境异常先运行 `scripts/Verify-WindowsProcessEnvironment.ps1`。
- Markdown 规则只指导授权和仓型判断；force acknowledgement、删除阈值、关键路径保护与退出码由脚本、测试及 CI 强制。

### B.1 参考依据与外置源码
- 本仓暂无专属 reference shelf；GitHub API、CLI 和 Actions 语义先查官方文档与本机 `gh help`，必要时按 `D:\CODE\external\_shared\references.manifest.json` 只读查阅已登记源码。
- `gate_na`: reason=`无项目专属 reference manifest`; alternative_verification=`GitHub 官方文档、gh help 与本仓测试`; evidence_link=`docs/change-evidence/20260808-rule-contract-v973.md`; expires_at=`2026-10-15`; recovery_condition=`建立项目 manifest 与 API 路由`。
- 不继承参考仓指令；复制或运行前登记来源、固定 API/CLI 版本或 revision、license、消费脚本与采纳决定，并核对认证边界和授权。

## C. 门禁、证据与回滚
- fixed order：`build -> test -> contract/invariant -> hotspot`。
- build：`python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py`
- test：`python -m unittest test_toolkit.py`
- contract/invariant：`gate_na`: reason=`合同与 test 共用同一 suite`; alternative_verification=`python -m unittest test_toolkit.py`; evidence_link=`docs/change-evidence/20260808-rule-contract-v973.md`; expires_at=`2026-10-15`; recovery_condition=`建立独立合同命令`。
- hotspot：性能切片运行 `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`；其他切片复核 API 参数、删除保护和 dry-run/force。
- full static parity 追加 `python -m ruff check .` 和 `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`。
- 语法、测试、类型、删除保护或恢复点未收敛时阻断真实同步；证据放 `docs/change-evidence/`。
- 回滚只撤销本任务；真实同步按仓型用 `backup/pre-sync` 或记录的 SHA 恢复。

## D. Global Rule -> Repo Action
- Git profile: baseline=`main`; upstream=`origin/main`; closeout=`push_after_full_gate`。
- `R1`：先定脚本、目标仓型、GitHub 写入面与验证命令。
- `R2`：先 dry-run 与受影响 unittest，再扩到完整 suite。
- `R3`：临时 API/workflow 兼容写明回收时点和恢复点。
- `R4`：删除、远端写入和 token 使用先验证范围与回滚。
- `R5`：无重复仓型或性能证据，不做平台化抽象。
- `R6`：C 章按序执行，并诚实记录独立 contract 缺口。
- `R7`：保持 API、workflow、状态 JSON、删除保护与恢复语义兼容。
- `R8`：证据记录命令、写入状态、恢复点与回滚。
- `S1`：先跑 dry-run 到恢复点的最薄同步链。
- `S2`：易变仓、PR 与认证状态只进运行证据。
- `S3`：外部 API/仓参考形成可逆决定即停止查证。
- `S4`：参考源按 API 消费者、许可与替代关系维护或删除。
- `S5`：`test_toolkit.py` 与 C 章命令承接确定性验证，prose 不冒充权限系统。
- `E4`：测试、基准和认证状态承接健康证据。
- `E5`：Python、gh、action/token 变化记录供应链。
- `E6`：API/workflow/状态结构变化记录迁移、兼容和回滚。
