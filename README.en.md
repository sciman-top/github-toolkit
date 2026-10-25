# GitHub Toolkit

<p align="center">
  <b>🛡️ Production-Ready GitHub Fork Safety Maintenance & Release Asset Synchronization Toolkit</b><br>
  <i>Zero external dependencies · Defense-in-depth safety guardrails · Automatic restore points · Release asset mirroring & smart latest.json rewriting</i>
</p>

<p align="center">
  <a href="https://github.com/sciman-top/github-toolkit/actions/workflows/ci.yml"><img src="https://github.com/sciman-top/github-toolkit/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <img src="https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-zero%20(stdlib%20only)-brightgreen.svg" alt="Zero Dependencies">
  <img src="https://img.shields.io/badge/gh%20cli-official-2088FF.svg?logo=github" alt="GitHub CLI">
  <img src="https://img.shields.io/badge/tests-58%20passed-success.svg" alt="Unit Tests">
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <a href="README.en.md">English</a>
</p>

---

## Why GitHub Toolkit?

When maintaining open-source forks for internal customizations, security hardening, custom CI builds, or domestic network mirrors, developers and platform teams frequently encounter critical challenges:

- **GitHub's native "Sync fork" lacks safety guardrails**: GitHub's web UI blindly executes a merge. If upstream gets wiped, compromised, or mistakenly deletes core directories (e.g., `.github/`, `README`, or `LICENSE`), native sync propagates the disaster directly into your fork, destroying your customizations.
- **No recovery checkpoint**: Native synchronization directly overwrites the default branch without creating a backup, leaving recovery dependent on searching through Git reflogs.
- **Release assets are completely left behind**: Git only synchronizes code commits, never GitHub Releases. When upstream publishes new installer packages (`.exe`, `.dmg`, `.tar.gz`, `.deb`), your fork's releases remain empty. Client auto-updaters relying on `latest.json` continue pointing to upstream URLs instead of your mirror.
- **Third-party Marketplace Actions carry severe supply-chain risks**: Most community fork-sync actions require personal access tokens (PAT) with broad repository write permissions, exposing organizations to credential leakage and supply chain attacks.

**GitHub Toolkit** is engineered to eliminate these pain points. Built with 100% Python standard library and the official GitHub CLI (`gh`), it delivers defense-in-depth safety circuit breakers, full-batch non-mutating preflight checks, automated moving restore points (`backup/pre-sync`), release asset mirroring, and smart `latest.json` URL rewriting with checksum manifest synchronization.

---

## Feature Comparison Matrix

| Feature | GitHub Web "Sync fork" | Community Marketplace Actions | GitHub Toolkit |
| :--- | :---: | :---: | :---: |
| **Batch Multi-Fork Discovery** | ❌ Manual click per repo | ❌ Configured per repo | ✅ **Automatic concurrent account discovery** |
| **Upstream Wipeout Protection** | ❌ Blind merge | ❌ None | ✅ **Immediate abort if files drop < 50%** |
| **Mass-Deletion Protection** | ❌ Uncapped | ❌ Uncapped | ✅ **Dynamic deletion budget ceiling** |
| **Critical Path Deletion Guard** | ❌ None | ❌ None | ✅ **Guards `.github/`, `README`, `LICENSE`** |
| **API Truncation Guard** | ❌ None | ❌ None | ✅ **Aborts on 300-entry compare payload** |
| **Automatic Restore Checkpoint** | ❌ None | ❌ None | ✅ **Refreshes `backup/pre-sync` before write** |
| **Full-Batch Preflight Validation** | ❌ None | ❌ None | ✅ **Any failure halts the entire batch** |
| **Release Asset Mirroring** | ❌ None | ❌ None | ✅ **Mirrors binaries, signatures & manifests** |
| **`latest.json` URL Rewriting** | ❌ None | ❌ None | ✅ **Rewrites download URLs to fork domain** |
| **Checksum Manifest Recalculation** | ❌ None | ❌ None | ✅ **Recalculates SHA256/512 and uploads bundle** |
| **Proven Mirror Asset Pruning** | ❌ None | ❌ None | ✅ **Safely prunes old mirrors, retains custom assets** |
| **Supply Chain Safety** | Official GitHub feature | Untrusted 3rd-party images/scripts | ✅ **100% Python stdlib + official `gh` CLI** |

---

## Safe Sync Pipeline Architecture

Every default branch synchronization strictly follows a six-stage defensive pipeline:

```text
 ┌────────────────────────────────────────────────────────┐
 │ 1. Fresh Runtime Discovery                             │
 │    Fetches forks, upstreams, and default branches live │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 2. Full-Batch Preflight Check                          │
 │    Simulates all safety checks before any writes start │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 3. Defense-in-Depth Safety Guardrails                  │
 │    • Upstream file count < 50% of fork ➔ Safety abort  │
 │    • Deletions > dynamic budget (5 + files/10) ➔ Abort │
 │    • Deletion of .github/, README, LICENSE ➔ Abort     │
 │    • Compare diff reaches 300 entries ➔ Truncate abort │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 4. Moving Restore Point Checkpoint                     │
 │    Forces backup/pre-sync branch to current fork HEAD  │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 5. Merge Upstream & Convergence Verification           │
 │    Calls merge-upstream, re-checks behind_by == 0      │
 └──────────────────────────┬─────────────────────────────┘
                            │
 ┌──────────────────────────▼─────────────────────────────┐
 │ 6. Release Asset Convergence                           │
 │    Mirrors binaries, rewrites latest.json, updates     │
 │    checksums, and safely prunes proven mirror assets   │
 └────────────────────────────────────────────────────────┘
```

---

## 30-Second Quick Start

### 1. Prerequisites

Requires Python 3.11+ and the official [GitHub CLI (`gh`)](https://cli.github.com/):

```powershell
# Windows users can install via winget
winget install --id GitHub.cli --source winget

# Authenticate GitHub account
gh auth login
```

> **Credential Precedence**: In CI/automation environments, `GH_TOKEN` takes precedence over `GITHUB_TOKEN`. For local interactive use, the CLI transparently uses your `gh auth login` keychain. Tokens are never logged or stored in plain text.

### 2. Essential Commands

#### ① List All Fork Repositories and Status (Read-Only)

```powershell
# Format output as clean console table
python list-forks.py

# Output structured JSON (for automation pipelines or dashboards)
python list-forks.py --json
```

Example output:

```text
Fork repos for your-name (3 total):

Discovery: live_github_api /user/repos at 2026-10-25T12:00:00Z (repos=12, forks=3)

Repository                          Branch     Upstream                            Behind   Workflow
-------------------------------------------------------------------------------------------------------
my-awesome-fork                     main       upstream-org/my-awesome-fork        2        Installed
devtools-mirror                     master     original/devtools-mirror            0        Missing
cockpit-custom                      main       cockpit-project/cockpit             Unknown  Installed
```

#### ② Dry-Run Preview

Always preview planned changes before applying real writes:

```powershell
# Preview sync for all forks in the account
python sync-forks.py --dry-run

# Preview sync for a single repository
python sync-forks.py my-awesome-fork --dry-run
```

#### ③ Execute Safe Synchronization

```powershell
# Synchronize all forks
python sync-forks.py

# Synchronize specific forks (accepts repo-name or owner/repo-name)
python sync-forks.py my-awesome-fork other-owner/another-fork
```

#### ④ Batch Install or Update Sync Workflows

Deploy or non-destructively upgrade managed `sync-fork.yml` workflows across all forks:

```powershell
# Preview workflow changes
python sync-forks.py --add-workflow --dry-run

# Apply workflow installation
python sync-forks.py --add-workflow
```

#### ⑤ Audit Scheduled Workflow Readiness & Secrets

```powershell
python sync-forks.py --verify-schedule
```

#### ⑥ Mirror and Reconcile Release Assets

```powershell
# Audit release mirror differences (read-only)
python sync-forks.py --verify-releases

# Dry-run asset reconciliation
python sync-forks.py --sync-releases --dry-run

# Apply release synchronization
python sync-forks.py --sync-releases my-awesome-fork
```

---

## Command-Line Reference

### 1. `list-forks.py`

| Flag | Description |
| :--- | :--- |
| *(none)* | Print table of all account forks, upstream delta, and workflow status |
| `--json` | Emit machine-readable JSON including auth principal, discovery metadata, and per-repo rows |
| `-h, --help` | Display command help |

> **Strict State Integrity**: When API limits or permissions prevent verification, the tool reports `Unknown` with precise error diagnostics rather than falsely claiming `Missing`.

### 2. `sync-forks.py`

| Option / Flag | Category | Description |
| :--- | :---: | :--- |
| `[repo-name ...]` | Target Selection | Specify one or more repositories (`repo` or `owner/repo`), deduplicated preserving order; omit to target all forks |
| `--dry-run` | Simulation | Execute all real API discovery and safety checks without modifying GitHub state |
| `--add-workflow` | Workflow Deployment | Create or update `.github/workflows/sync-fork.yml` across targeted forks |
| `--verify-schedule` | Audit | Verify workflow installation, `SYNC_PAT` secret presence, and scheduled run histories |
| `--verify-releases` | Audit | Compare upstream vs fork stable release assets and retention policies |
| `--sync-releases` | Reconciliation | Mirror stable binaries, rewrite `latest.json`, and prune proven old mirror assets |
| `--force` | Force Override | Bypass safety checks or force workflow rewrite (requires explicit confirmation env var) |
| `--json` | Output Control | Emit detailed JSON report with run summary, preflight status, and per-repo results |
| `-h, --help` | Help | Display CLI help and examples |

### 3. Force Write Acknowledgement (`--force`)

To prevent catastrophic accidental writes, real mutating runs using `--force` require explicit environment acknowledgement:

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

### 4. Exit Code Contract

Scripts strictly adhere to deterministic exit codes for reliable CI/CD pipeline integration:

- `0`: Operation succeeded, or no changes required.
- `1`: CLI unavailable, invalid credentials, GitHub API error, or CLI argument syntax error; also returned if `list-forks.py` encountered incomplete repo checks.
- `3`: Safety circuit breaker triggered, or read-only audit discovered pending actions requiring manual or mutating follow-up.

---

## Release Asset Synchronization & Rewriting

Modern desktop applications (e.g., built with Tauri or Electron) rely on GitHub Releases for binary distribution and automatic updates. Standard Git forks only sync code branches, leaving release mirrors empty.

`--sync-releases` provides a complete, automated mirroring solution:

```text
Upstream Release (v1.2.0)                      Fork Release (v1.2.0)
 ├── app-setup.exe         ─── Mirror ──────►   ├── app-setup.exe
 ├── app-setup.dmg         ─── Mirror ──────►   ├── app-setup.dmg
 ├── latest.json           ─── Rewrite ─────►   ├── latest.json (URL rewritten to Fork repository)
 └── SHA256SUMS.txt        ─── Recalculate ─►   └── SHA256SUMS.txt (Manifest updated with new hash)
```

1. **Whitelisted Asset Matching**: Matches binary installers (`.exe`, `.msi`, `.deb`, `.rpm`, `.appimage`, `.dmg`, `.pkg`, `.zip`, `.tar.gz`), signatures (`.sig`, `.minisig`), and checksum manifests (`sha256sums*`, `sha512sums*`, `latest.json`).
2. **`latest.json` URL Rewriting**: Automatically rewrites upstream release download URLs to point to your fork repository.
3. **Manifest Recalculation**: Automatically updates `SHA256SUMS.txt` or `SHA512SUMS.txt` with the newly computed digest for `latest.json`.
4. **Atomic Bundle Upload**: Uploads `latest.json` and checksum manifests together to avoid windowed verification failures.
5. **Asset Name Sanitation**: Rejects unsafe names containing path separators (`/`, `\`), NUL bytes (`\0`), directory traversal (`..`), or leading flag hyphens (`-`).
6. **Safe Mirror Pruning**: Prunes only assets rigorously proven to be upstream mirrors (matching name + digest or name + size). **Custom tags and user-uploaded fork assets are never touched.**

---

## GitHub Actions Automated Operations

The repository includes three complementary GitHub Actions workflows:

- **`ci.yml`**: Runs all 58 unit tests on push and pull requests in < 0.1s.
- **`auto-setup-forks.yml`**: Runs weekly on Sunday and on template updates to automatically deploy `sync-fork.yml` to all current and newly created forks.
- **`integration-api-smoke.yml`**: Runs weekly on Monday using dedicated read-only credentials to verify live GitHub API compatibility.

### Repository Secrets Configuration

Configure these repository secrets for automated maintenance:

| Secret Name | Required | Description |
| :--- | :---: | :--- |
| `SYNC_PAT` | Yes | Personal Access Token with `repo` and `workflow` scopes, used to commit workflow files and synchronize forks |
| `INTEGRATION_GH_TOKEN` | Optional | Dedicated read-only token for API smoke testing |
| `INTEGRATION_EXPECTED_LOGIN` | Optional | Guard against token principal mismatches |
| `INTEGRATION_TARGET_REPO` | Optional | Specific target fork repository name for dry-run smoke checks |

---

## Disaster Recovery & Rollback Guide

If an upstream repository suffered corruption that bypassed safety rules (or if `--force` was used), restore your fork within seconds:

```bash
# 1. Navigate to your local fork directory
cd path/to/your-fork

# 2. Fetch the automatic backup checkpoint branch
git fetch origin backup/pre-sync

# 3. Inspect commits in the backup branch
git log -n 5 origin/backup/pre-sync

# 4. Reset default branch to pre-sync state
git checkout main
git reset --hard origin/backup/pre-sync

# 5. Force-push to restore remote fork state
git push origin main --force
```

---

## Development & Testing

Run the full unit test suite locally (zero third-party dependencies, runs in ~0.08s):

```powershell
python -m unittest -q test_toolkit.py
```

Test coverage highlights:
- GitHub CLI detection and timeout error classification
- Credential resolution and concurrent upstream parent discovery
- Workflow template rendering and drift detection
- 50% file drop, deletion volume limit, and critical file deletion circuit breakers
- Compare 300-entry API truncation protection and moving restore point updates
- Force confirmation environment variable enforcement
- Release asset filtering, URL rewriting, and checksum recalculation
- Mirror provenance validation and safe asset pruning boundaries

---

## License

Distributed under the [MIT License](https://opensource.org/licenses/MIT). Issues and Pull Requests are warmly welcome!
