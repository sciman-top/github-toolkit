# AGENTS.md - github-toolkit
**项目契约**: 2.0
**全局规则复核**: 9.57
**最后更新**: 2026-07-15

## 1. 当前落点与目标归宿
- 当前落点：本仓是 Python GitHub fork/同步辅助工具，核心能力集中在 `gh_utils.py`、`sync-forks.py` 与 `list-forks.py`。
- 目标归宿：在明确 dry-run、删除保护、恢复点和认证边界下安全维护 fork/workflow，同步行为保持可审计、可回退。
- 下一最小里程碑：完成当前脚本 slice，并以静态检查、回归、合同缺口说明与 hotspot 证据收口。

## A. 仓库事实与模块边界
- `gh_utils.py`：GitHub CLI/API、认证与同步保护；`sync-forks.py`：workflow/同步/dry-run/force；`list-forks.py`：状态输出。
- `test_toolkit.py` 与 `test_support.py` 承载回归及受限环境 fallback；`scripts/benchmark_hotpaths.py` 承载合成热点基准。
- 真实同步必须先 dry-run；`--force` 需要 `SYNC_FORKS_FORCE_ACK=I_UNDERSTAND`。
- `upstream_default_branch` 仓保留移动恢复点 `backup/pre-sync`；stable-release/mirror-only 仓记录旧/新 SHA 与回滚命令，不长期创建 `backup/*`。
- `GH_TOKEN`、`GITHUB_TOKEN` 与本机 `gh auth` 是认证输入，不得进入仓库、日志或证据正文。

## B. 执行与风险边界
- setup/auth 入口不计门禁；认证缺失时优先完成无写入静态/测试证据，不得为通过门禁伪造线上状态。
- 任何真实默认分支、workflow 或 release 写操作属于中风险；执行前确认 dry-run、删除阈值、关键路径保护与恢复点。
- 新增依赖、API 字段或同步模式前说明必要性、向后兼容与 rollback；无证据不扩成通用平台。
- Windows 进程环境异常先运行 `scripts/Verify-WindowsProcessEnvironment.ps1`，不要直接归因于仓库逻辑。

## C. 门禁、证据与回滚
- fixed order：`build -> test -> contract/invariant -> hotspot`。
- build：`python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py`
- test：`python -m unittest test_toolkit.py`
- contract/invariant：当前没有独立命令；`gate_na` 记录 `reason=现有合同与 test 共用同一 suite`、`alternative_verification=python -m unittest test_toolkit.py`、`evidence_link=README.md#本地门禁固定顺序`、`expires_at=next_executable_change`、`recovery_condition=建立独立合同命令并纳入门禁`，不得把重复运行表述为独立证据。
- hotspot：触及性能路径运行 `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`；其他切片复核 API 参数、删除保护与 dry-run/force 边界。
- full static parity 追加 `python -m ruff check .` 与 `python -m mypy gh_utils.py list-forks.py sync-forks.py scripts/benchmark_hotpaths.py`。
- 语法、测试、类型、删除保护或恢复点未收敛时阻断真实同步/发布。
- 证据放入 `docs/change-evidence/`；记录风险、命令、exit code、是否有真实 GitHub 写入、旧/新 SHA 与回滚。
- 回滚优先撤销本任务代码/规则；真实同步按仓型使用 `backup/pre-sync` 或记录的 SHA 恢复，不得影响无关 fork。

## D. Global Rule -> Repo Action
- `R1-R5`：先定脚本/仓型/写入面与验证；先 dry-run 和小步测试，不做无证据平台化。
- `R6`：C 章按序执行，并诚实记录独立 contract 缺口。
- `R7`：保持 API、workflow、状态 JSON、删除保护与仓型恢复语义兼容。
- `R8`：证据记录命令、写入状态、恢复点与回滚。
- `E4`：测试/基准/认证状态承接健康；`E5`：Python、gh、action/token 变化记录供应链；`E6`：API/workflow/状态结构变化记录迁移、兼容和回滚。
