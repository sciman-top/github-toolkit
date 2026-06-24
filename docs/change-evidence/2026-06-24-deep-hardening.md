# 2026-06-24 深度硬化证据

- 规则 ID: `R1,R2,R6,R8,E4`
- 风险等级: `中`
- 当前落点: `D:\CODE\github-toolkit`
- 目标归宿: 在不改变 CLI / JSON / 远端写入契约的前提下，加固 `gh_utils.py`、`sync-forks.py`、`list-forks.py` 并补回归测试

## 依据

- 已确认 `sync-forks.py` 存在重复定义 `_mode_name`，会造成静默覆盖风险。
- release retention 资产同步对 `latest.json` 与 `SHA256SUMS.txt` 的联动校验不够严格，存在部分成功后继续上传的可能。
- `paginate()` 对非法输入缺少防御，`list-forks.py` 对缺失分支元数据会继续发起错误 API 请求。
- 目标是严格兼容加固，不新增依赖，不改变门禁命令、CLI flags、退出码和现有 JSON 关键字段。

## 变更摘要

- `gh_utils.py`
  - 抽出共享的 gh 子进程执行辅助函数。
  - 为 `paginate()` 增加 endpoint / per_page 防御校验，返回结构化错误。
- `sync-forks.py`
  - 删除重复 `_mode_name` 定义。
  - 拆出 compare payload 校验、标准化 safety abort 打印、旧 release 必删资产计数、release 文件改写/校验辅助函数。
  - release 资产同步改为 fail-closed：下载后必须验证文件存在，`latest.json` 改写后必须同步刷新并验证 `SHA256SUMS.txt` 中存在 `latest.json` 项，否则直接失败。
  - 当 `latest.json` 缺失且需要重写时，自动联动下载并重传 `SHA256SUMS.txt`。
- `list-forks.py`
  - 对缺失 `branch/full_name/parent_branch` 的场景做只读降级，不再发送错误 API 请求。
- `test_toolkit.py`
  - 新增 AST 守卫，禁止顶层重复定义。
  - 新增分页非法输入、模式映射、release retention 旧资产分类、release 资产同步失败路径、`list-forks` 异常降级等回归测试。

## 执行命令与结果

1. `python -m unittest test_toolkit.py`
   - 结果: `PASS`
   - 证据: `Ran 192 tests ... OK`

2. `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
   - 结果: `PASS`
   - 证据: 退出码 `0`

3. `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
   - 结果: `PASS`
   - 证据: `threshold_check.violations = []`

4. `python list-forks.py --json`
   - 结果: `PASS`
   - 证据: 返回 `tool=list-forks`，`summary.total=3`

5. `python sync-forks.py --json --verify-schedule`
   - 结果: `PASS`
   - 证据: `message = "Schedule readiness verified: ready 3/3."`

6. `python sync-forks.py --json --verify-releases`
   - 结果: `PASS`
   - 证据: `message = "Release retention verified: ready 3/3."`

## N/A

- `platform_na`: 无
- `gate_na`: 无

## 回滚入口

- Git 回滚: 使用当前分支 `codex/deep-hardening` 上的提交历史回退。
- 文件级回滚范围:
  - `D:\CODE\github-toolkit\gh_utils.py`
  - `D:\CODE\github-toolkit\sync-forks.py`
  - `D:\CODE\github-toolkit\list-forks.py`
  - `D:\CODE\github-toolkit\test_toolkit.py`

## 备注

- 本次未执行任何真实 GitHub 写操作；仅运行只读 `--verify-*` 和 `list-forks` 烟测。
- 工作流和 release 的真实远端行为兼容性通过现有单测、热点基准和只读烟测间接验证。
