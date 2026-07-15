# Agent Rule Governance 9.57

## Scope and boundary

- repository: `github-toolkit`
- frozen baseline: `811afc3c04d47c3c829a5f6b19d99a1b0b2ceeab`
- task branch: `codex/agent-rule-governance-9.57`
- write-set: `AGENTS.md` and this evidence file; `CLAUDE.md` remains the verified import-only wrapper
- release review: `rule_release=9.57 / project_contract_version=2.0 / coordination_schema=2.3`
- semantic basis: Claude Code's current official memory documentation permits imports up to five hops; the project WHERE/HOW contract itself is unchanged
- exclusions: no product/runtime/schema/data/dependency/auth/provider/secret/MCP/account/process/hosted-UI change

## Verification ledger

- wrapper: `CLAUDE.md` verified as the import-only `@AGENTS.md` wrapper, no BOM; control-repo `--require-all` target audit passed for all 9 isolated targets
- build: Python compilation passed
- test: `python -m unittest test_toolkit.py` passed, 189 tests
- contract/invariant: `gate_na`; reason=`this repository intentionally shares the executable contract with the unittest suite`; alternative_verification=`189-test suite plus target-rule verifier`; evidence_link=`README.md local gate and this record`; expires_at=`next_executable_change`; recovery_condition=`an independent contract command is added`
- hotspot: `gate_na`; reason=`rule-marker-only slice has no runtime performance path`; alternative_verification=`API/deletion/dry-run review plus diff check`; evidence_link=`this record`; expires_at=`next_executable_change`; recovery_condition=`runtime or performance-sensitive code changes`
- optional lint/type tools: `platform_na`; reason=`ruff and mypy are not installed in this repository environment`; alternative_verification=`Python compilation, 189 tests, and diff hygiene`; evidence_link=`this record`; expires_at=`next toolchain provisioning`; recovery_condition=`tools become repo-declared dependencies`
- five-axis review: correctness/readability/architecture/security/performance passed with no Critical or Required finding
- Git publication: not yet executed at this capture point

## Compatibility and rollback

- compatibility: content-release review marker only; repository commands, invariants, external behavior, data formats, and wrapper loading shape remain unchanged
- rollback: revert only `AGENTS.md` and this evidence file from the task commit; do not reset, clean, or include unrelated local history

## Completion boundary at capture

- `repo-side completed=true`
- `published branch=false`
- `default-branch effective=false`
- `hosted/manual accepted=false`
- `fully completed=false`
