# Weekly Stable Release Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `latest_stable_assets_only` fork 增加手动和每周自动的稳定版 release 收敛能力，同时明确排除 `cockpit-tools-local` 自用版 weekly release。

**Architecture:** 在 `sync-forks.py` 中新增 `--sync-releases` 模式，复用现有 release 核验逻辑做策略判定，再补充最小远端执行器；同时更新 workflow 模板，让 fork 仓库 weekly job 直接执行等价的 release 收敛逻辑。

**Tech Stack:** Python 3, `gh api`, `gh release`, GitHub Actions, `unittest`

---

### Task 1: Extend Release Verification

**Files:**
- Modify: `sync-forks.py`
- Test: `test_toolkit.py`

- [ ] Add failing tests for latest stable asset coverage instead of count-only checks.
- [ ] Run the targeted tests and confirm they fail for the missing behavior.
- [ ] Implement asset-name coverage helpers and wire them into `--verify-releases`.
- [ ] Re-run the targeted tests and confirm they pass.

### Task 2: Add Local `--sync-releases`

**Files:**
- Modify: `sync-forks.py`
- Test: `test_toolkit.py`

- [ ] Add failing CLI/mode tests for `--sync-releases`.
- [ ] Add failing operation tests for create/upload/prune/skip behavior.
- [ ] Implement release sync mode, helpers, and summary/exit-code handling.
- [ ] Re-run the focused tests and confirm they pass.

### Task 3: Extend Generated Workflow

**Files:**
- Modify: `sync-forks.py`
- Test: `test_toolkit.py`

- [ ] Add failing workflow contract tests for the weekly release retention step.
- [ ] Update the workflow template with release retention execution.
- [ ] Re-run workflow contract tests and confirm they pass.

### Task 4: Update Integration and Docs

**Files:**
- Modify: `.github/workflows/integration-api-smoke.yml`
- Modify: `README.md`
- Create: `docs/change-evidence/2026-06-06-release-sync.md`

- [ ] Extend integration smoke with release verify / dry-run coverage.
- [ ] Update README command and behavior docs.
- [ ] Record evidence, risk boundary, and rollback entry.

### Task 5: Full Verification

**Files:**
- Modify: `sync-forks.py`
- Modify: `test_toolkit.py`
- Modify: `.github/workflows/integration-api-smoke.yml`
- Modify: `README.md`

- [ ] Run `python -m py_compile gh_utils.py list-forks.py sync-forks.py test_toolkit.py test_support.py scripts/benchmark_hotpaths.py .claude/hooks/governed-pre-tool-use.py`
- [ ] Run `python -m unittest test_toolkit.py`
- [ ] Run `python sync-forks.py --json --verify-releases`
- [ ] Run `python sync-forks.py --json --sync-releases --dry-run`
- [ ] If all checks pass, summarize remaining risks and hand back a concise report.
