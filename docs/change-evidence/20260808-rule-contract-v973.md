# Global rule 9.73 project-contract evidence

- Repository: `github-toolkit`
- Scope: project rule mapping only; no business-code or host-runtime mutation.
- Official basis: current Codex AGENTS loading/precedence and rules semantics; Claude platform delta remains separately verified.
- Git profile: baseline=`main`; upstream=`origin/main`.
- Before AGENTS SHA-256: `AA541FCAE142836CD6AEFC8CCEE3A4FE688972BA07C9F7E94F4C20285B4E1A71`
- After AGENTS SHA-256: `A7E7826471AB1D7D7E0959E2AEEF4D9FAF7A21B3396D6893C002008CB895C9A8`
- Planned gate: `python -m unittest test_toolkit.py`
- Current verification: py_compile and 189/189 unittest passed; full-static add-on stopped because host Python lacks `ruff`, so mypy was not run.
- N/A: reason=`pure rule slice and optional full-static tool absent`; alternative_verification=`py_compile, unittest, RuleEstate and git diff --check`; expires_at=`2026-10-15`; recovery_condition=`ruff is available or executable code changes`.
- N/A: host loading and live acceptance remain outside repository-static verification.
- Rollback: revert only this repository's `AGENTS.md` and this evidence file to the recorded before hash.
- Truth boundary: `repo_verified=rule_contract_only`; `full_gate=not_completed`; `host_loaded=codex_fresh_prompt_verified`; `claude_loaded=not_run`; `live_accepted=not_run`.
