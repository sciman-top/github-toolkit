# 2026-04-29 Python Write/Compile Probe Hardening

## Goal
将 Python 写入与 `py_compile` 权限阻塞从一次性现象转成可复跑诊断，纳入 `scripts/Verify-WindowsProcessEnvironment.ps1`。

## Changes
- 扩展 `scripts/Verify-WindowsProcessEnvironment.ps1`：
  - 新增参数 `-SkipPythonWriteProbe`
  - 新增 `python write probe (workspace)` 检查
  - 新增 `python py_compile probe` 检查
- 更新 `README.md`：补充新增探针与 `-SkipPythonWriteProbe` 用法
- 修复 `test_toolkit.py`：
  - 移除临时目录依赖，改为 mock `gh_utils._read_codex_shell_environment_policy_set`
  - 新增 `_load_module()` 显式处理 `spec/loader` 可空路径，消除 mypy 告警

## Verification (fixed order)
1. build
- `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- result: pass

2. test
- `python -m unittest test_toolkit.py`
- result: pass (`Ran 141 tests ... OK`)

3. contract/invariant
- `python -m unittest test_toolkit.py`
- result: pass

4. hotspot
- `python scripts/benchmark_hotpaths.py --sizes 10,100,500 --runs 5 --check`
- result: pass (`violations: []`)

## Platform diagnostics
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Verify-WindowsProcessEnvironment.ps1 -SkipGh -Json`
- key checks:
  - `python write probe (workspace)`: ok
  - `python py_compile probe`: ok

## N/A
- none

## Rollback
- `git checkout -- scripts/Verify-WindowsProcessEnvironment.ps1 README.md test_toolkit.py`
