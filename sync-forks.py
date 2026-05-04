#!/usr/bin/env python3
"""
sync-forks.py — Sync fork repos from upstream with safety checks.

Safety mechanism:
  Before syncing, compares file counts between fork and upstream.
  If upstream has significantly fewer files (suggesting deletion/wipe),
  the sync is ABORTED. Threshold: upstream < 50% of fork's file count.

NOTE: Authentication prefers GH_TOKEN/GITHUB_TOKEN and falls back to
      gh auth login local configuration. No passwords/tokens are handled
      in this code directly. See README.md "认证方式" section for details.

Usage:
  python sync-forks.py                  # Sync all forks now
  python sync-forks.py repo-name        # Sync specific repo (owner/name or just name)
  python sync-forks.py --add-workflow   # Add/update sync-fork.yml to repos
  python sync-forks.py --dry-run        # Preview only
  python sync-forks.py --json           # Emit machine-readable summary
  python sync-forks.py --force          # Skip safety checks (requires explicit confirmation)
"""

import base64
import contextlib
import io
import json
import os
import re
import sys

from gh_utils import (
    AuthContextError,
    DELETION_THRESHOLD,
    ForkDiscoveryError,
    SYNC_CRON,
    build_compare_endpoint,
    build_fork_record,
    check_gh_cli,
    encode_url_component,
    get_compare_head,
    get_auth_context,
    get_file_count,
    get_fork_repos_with_evidence,
    gh_api,
    parse_nonnegative_int,
)

# ── Exit codes ────────────────────────────────────────────────
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SAFETY = 3

# ── safety_check return values ────────────────────────────────
SAFE = True      # safe to sync
ABORT = False    # unsafe, sync must be aborted
SKIP = None      # upstream unavailable, skip without error

# ── Operation statuses ────────────────────────────────────────
RESULT_OK = "ok"
RESULT_SAFETY = "safety"
RESULT_ERROR = "error"
FORCE_SYNC_ACK_ENV = "SYNC_FORKS_FORCE_ACK"
FORCE_SYNC_ACK_VALUE = "I_UNDERSTAND"
WORKFLOW_DISPATCH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])workflow_dispatch(?![A-Za-z0-9_-])"
)
WORKFLOW_DISPATCH_KEY_RE = re.compile(
    r"^(?:-\s*)?workflow_dispatch(?:\s*:|\s*$)"
)

HELP_TEXT = """\
Usage:
  python sync-forks.py [options] [repo-name ...]

Options:
  --add-workflow   Add or update sync-fork.yml instead of syncing now
  --verify-schedule Verify workflow/secret/scheduled-run readiness
  --dry-run        Preview planned changes without modifying repos
  --force          Bypass safety checks (sync needs explicit confirmation)
  --json           Emit a machine-readable JSON summary
  -h, --help       Show this help message

Force confirmation for real sync:
  set %SYNC_FORKS_FORCE_ACK%=I_UNDERSTAND   (Windows cmd)
  $env:SYNC_FORKS_FORCE_ACK = "I_UNDERSTAND" (PowerShell)

Examples:
  python sync-forks.py
  python sync-forks.py repo-name
  python sync-forks.py owner/repo-name --dry-run
  python sync-forks.py --add-workflow
"""

# ── Workflow template ─────────────────────────────────────────

WORKFLOW_TEMPLATE = '''\
name: Sync Fork
on:
  schedule:
    - cron: "{cron}"
  workflow_dispatch: {{}}

permissions:
  contents: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout fork
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{{{ secrets.SYNC_PAT }}}}

      - name: Resolve upstream
        id: upstream
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
        run: |
          set -euo pipefail
          UPSTREAM_FULL_NAME=$(gh api repos/${{{{ github.repository }}}} --jq '.parent.full_name // empty')
          if [ -z "$UPSTREAM_FULL_NAME" ]; then
            echo "::warning::Upstream repo metadata is missing. Sync skipped to preserve fork."
            echo "skip=true" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          if ! gh api "repos/$UPSTREAM_FULL_NAME" >/dev/null 2>&1; then
            echo "::warning::Upstream repo $UPSTREAM_FULL_NAME is unavailable or deleted. Sync skipped to preserve fork."
            echo "skip=true" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          UPSTREAM_BRANCH=$(gh api "repos/$UPSTREAM_FULL_NAME" --jq '.default_branch // empty')
          UPSTREAM_URL=$(gh api "repos/$UPSTREAM_FULL_NAME" --jq '.clone_url')
          echo "full_name=$UPSTREAM_FULL_NAME" >> "$GITHUB_OUTPUT"
          echo "owner=${{UPSTREAM_FULL_NAME%%/*}}" >> "$GITHUB_OUTPUT"
          echo "branch=$UPSTREAM_BRANCH" >> "$GITHUB_OUTPUT"
          echo "url=$UPSTREAM_URL" >> "$GITHUB_OUTPUT"

      - name: Safety check - compare file counts
        if: steps.upstream.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
        run: |
          set -euo pipefail
          git remote add upstream "${{{{ steps.upstream.outputs.url }}}}"
          git fetch upstream "${{{{ steps.upstream.outputs.branch }}}}"
          FORK_FILES=$(git ls-tree -r --name-only HEAD | wc -l | tr -d '[:space:]')
          UPSTREAM_FILES=$(git ls-tree -r --name-only "upstream/${{{{ steps.upstream.outputs.branch }}}}" | wc -l | tr -d '[:space:]')
          THRESHOLD=$(( (FORK_FILES + 1) / 2 ))
          echo "Fork files: $FORK_FILES"
          echo "Upstream files: $UPSTREAM_FILES"
          if [ "$UPSTREAM_FILES" -lt "$THRESHOLD" ]; then
            echo "::error::SAFETY ABORT: Upstream has $UPSTREAM_FILES files vs fork's $FORK_FILES. Possible upstream deletion. Sync skipped."
            exit 1
          fi
          echo "Safety check passed."

      - name: Create restore point
        if: steps.upstream.outputs.skip != 'true'
        run: |
          set -euo pipefail
          BACKUP_BRANCH="backup/pre-sync"
          git branch -f "$BACKUP_BRANCH" HEAD
          git push origin "$BACKUP_BRANCH" --force

      - name: Diff guard - inspect deletions
        if: steps.upstream.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
          BASE_REF: ${{{{ github.event.repository.default_branch }}}}
          HEAD_REF: ${{{{ steps.upstream.outputs.owner }}}}:${{{{ steps.upstream.outputs.branch }}}}
        run: |
          set -euo pipefail
          FORK_FILES=$(git ls-tree -r --name-only HEAD | wc -l | tr -d '[:space:]')
          COMPARE_BASE=$(python3 -c 'import os; from urllib.parse import quote; print(quote(os.environ["BASE_REF"], safe=":"))')
          COMPARE_HEAD=$(python3 -c 'import os; from urllib.parse import quote; print(quote(os.environ["HEAD_REF"], safe=":"))')
          COMPARE_JSON=$(gh api "repos/${{{{ github.repository }}}}/compare/${{COMPARE_BASE}}...${{COMPARE_HEAD}}")
          TOTAL_DELETIONS=$(printf '%s' "$COMPARE_JSON" | jq -r '.total_deletions // 0')
          DELETED_FILES=$(printf '%s' "$COMPARE_JSON" | jq -r '.files[]? | select(.status == "removed") | .filename')
          DELETE_LIMIT=$(( 5 + (FORK_FILES / 10) ))
          echo "Total deletions: $TOTAL_DELETIONS"
          echo "Deletion limit: $DELETE_LIMIT"
          if [ "$TOTAL_DELETIONS" -gt "$DELETE_LIMIT" ]; then
            echo "::error::SAFETY ABORT: deletion volume is too large for a safe sync."
            exit 1
          fi
          if printf '%s\\n' "$DELETED_FILES" | grep -Eq '^(\\.github/|README\\.md$|LICENSE$|sync-fork\\.yml$)'; then
            echo "::error::SAFETY ABORT: critical path deletion detected."
            exit 1
          fi

      - name: Merge upstream changes
        if: steps.upstream.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
        run: |
          set -euo pipefail
          gh api repos/${{{{ github.repository }}}}/merge-upstream -X POST -f branch=${{{{ github.event.repository.default_branch }}}}
'''


# ── Core functions ────────────────────────────────────────────

def _decode_workflow_content(existing_file):
    """Decode GitHub contents API payload text, return None on failure."""
    if not isinstance(existing_file, dict):
        return None
    encoded = existing_file.get("content")
    if not encoded:
        return None
    try:
        raw = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None
    return raw


def _has_workflow_dispatch(workflow_text):
    """Best-effort trigger detection without YAML parser dependency."""
    if not workflow_text:
        return False
    in_on_block = False
    on_indent = None
    for line in workflow_text.splitlines():
        line_without_comment = line.split("#", 1)[0].rstrip()
        stripped = line_without_comment.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line_without_comment) - len(line_without_comment.lstrip())

        if in_on_block and indent <= on_indent:
            in_on_block = False
            on_indent = None

        if stripped.startswith("on:"):
            on_value = stripped[3:].strip()
            if WORKFLOW_DISPATCH_TOKEN_RE.search(on_value):
                return True
            if not on_value:
                in_on_block = True
                on_indent = indent
            continue

        if in_on_block and WORKFLOW_DISPATCH_KEY_RE.search(stripped):
            return True
    return False


def _missing_workflow_template_markers(workflow_text):
    """Return current-template markers missing from an existing workflow."""
    required_markers = (
        f'cron: "{SYNC_CRON}"',
        "Resolve upstream",
        "Create restore point",
        "Diff guard - inspect deletions",
        "backup/pre-sync",
        "critical path deletion detected",
        "merge-upstream",
        "merge-upstream -X POST -f branch=${{ github.event.repository.default_branch }}",
    )
    return [
        marker
        for marker in required_markers
        if marker not in workflow_text
    ]


def _needs_workflow_update(existing_file):
    """Return (needs_update: bool, reason: str)."""
    if not isinstance(existing_file, dict):
        return True, "invalid workflow metadata payload"

    if "error" in existing_file:
        if not _is_not_found_error(existing_file):
            return True, "cannot verify existing workflow"
        return True, "missing file"

    workflow_text = _decode_workflow_content(existing_file)
    if workflow_text is None:
        return True, "cannot decode existing workflow"

    if not _has_workflow_dispatch(workflow_text):
        return True, "missing workflow_dispatch trigger"

    missing_markers = _missing_workflow_template_markers(workflow_text)
    if missing_markers:
        preview = ", ".join(missing_markers[:3])
        if len(missing_markers) > 3:
            preview += ", ..."
        return True, f"workflow template drift: missing {preview}"

    return False, "already matches current workflow template"


def _short_error(payload, default="invalid response payload"):
    """Return a bounded, printable error detail for CLI logs."""
    if isinstance(payload, dict):
        return str(payload.get("error", default))[:200]
    return default


def safety_check(fork_name, fork_branch, upstream_name, upstream_branch,
                 force=False):
    """Compare file counts and deletions. Return SAFE/ABORT/SKIP."""
    if force:
        print("  [FORCE] Safety check bypassed.")
        return SAFE

    fork_count = get_file_count(fork_name, fork_branch)
    upstream_count = get_file_count(upstream_name, upstream_branch)

    if fork_count is None:
        print("  *** SAFETY ABORT ***")
        print("  Cannot get fork file count, safety baseline is unavailable.")
        print("  Sync skipped to preserve fork.")
        print("  Use --force to override (NOT recommended).")
        return ABORT
    if upstream_count is None:
        print("  WARN: Upstream unavailable or deleted. Skipping sync to preserve fork.")
        return SKIP

    print(f"  Files: fork={fork_count}, upstream={upstream_count}")

    if upstream_count < fork_count * DELETION_THRESHOLD:
        print("  *** SAFETY ABORT ***")
        print(f"  Upstream has {upstream_count} files, fork has {fork_count}.")
        print("  Upstream may have been wiped. Sync skipped for safety.")
        print("  Use --force to override (NOT recommended).")
        return ABORT

    compare_head = get_compare_head(upstream_name, upstream_branch)
    compare_endpoint = build_compare_endpoint(fork_name, fork_branch, compare_head)
    if not compare_endpoint:
        print("  ABORT: Upstream metadata is incomplete, cannot inspect diff safely.")
        return ABORT
    compare = gh_api(compare_endpoint)
    if not isinstance(compare, dict) or "error" in compare:
        print("  ABORT: Cannot inspect upstream diff safely.")
        return ABORT

    total_deletions = parse_nonnegative_int(compare.get("total_deletions", 0) or 0)
    if total_deletions is None:
        print("  ABORT: Compare response deletion count is malformed.")
        return ABORT

    files = compare.get("files", [])
    if not isinstance(files, list):
        print("  ABORT: Compare response files payload is malformed.")
        return ABORT

    deleted_files = []
    for item in files:
        if not isinstance(item, dict):
            print("  ABORT: Compare response file entry is malformed.")
            return ABORT
        if item.get("status") == "removed":
            filename = item.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                print("  ABORT: Compare response removed filename is malformed.")
                return ABORT
            deleted_files.append(filename)
    deletion_limit = 5 + (fork_count // 10)

    print(f"  Deletions: {total_deletions} (limit: {deletion_limit})")
    if total_deletions > deletion_limit:
        print("  *** SAFETY ABORT ***")
        print("  Deletion volume is too large for a safe sync.")
        return ABORT

    critical_deleted = [
        path for path in deleted_files
        if path.startswith(".github/") or path in {
            "README.md", "LICENSE", "sync-fork.yml",
        }
    ]
    if critical_deleted:
        print("  *** SAFETY ABORT ***")
        print("  Critical path deletions detected:")
        for path in critical_deleted:
            print(f"    - {path}")
        return ABORT

    return True


def _add_sync_workflow_result(repo_name, branch, dry_run=False, force=False):
    """Add or update sync-fork.yml in a repo. Return operation result."""
    encoded_branch = encode_url_component(branch)
    if not repo_name or not encoded_branch:
        print("  FAIL: Cannot verify existing sync-fork.yml: invalid repo or branch")
        return RESULT_ERROR

    existing = gh_api(
        f"repos/{repo_name}/contents/.github/workflows/sync-fork.yml"
        f"?ref={encoded_branch}"
    )
    if not isinstance(existing, dict):
        print("  FAIL: Cannot verify existing sync-fork.yml: invalid metadata payload")
        return RESULT_ERROR
    if "error" in existing and not _is_not_found_error(existing):
        print(f"  FAIL: Cannot verify existing sync-fork.yml: {existing['error'][:200]}")
        return RESULT_ERROR

    needs_update, reason = _needs_workflow_update(existing)
    has_existing = (
        isinstance(existing, dict)
        and "error" not in existing
        and bool(existing.get("sha"))
    )

    if has_existing and not needs_update and not force:
        print("  SKIP: sync-fork.yml already matches current workflow template")
        return RESULT_OK

    content = WORKFLOW_TEMPLATE.format(cron=SYNC_CRON)
    encoded = base64.b64encode(content.encode()).decode()

    if dry_run:
        action = "update" if has_existing else "add"
        print(
            f"  [DRY-RUN] Would {action} sync-fork.yml "
            f"(branch: {branch}, reason: {reason})"
        )
        return RESULT_OK

    payload = {
        "message": "ci: update fork sync workflow with safety checks",
        "content": encoded,
        "branch": branch,
    }
    if has_existing:
        payload["sha"] = existing.get("sha")

    result = gh_api(
        f"repos/{repo_name}/contents/.github/workflows/sync-fork.yml",
        method="PUT",
        payload=payload,
    )

    if not isinstance(result, dict):
        print("  FAIL: Invalid workflow update response.")
        return RESULT_ERROR
    if "error" in result:
        print(f"  FAIL: {_short_error(result)}")
        return RESULT_ERROR

    url = result.get("content", {}).get("html_url", "ok")
    action = "Updated" if has_existing else "Added"
    print(f"  {action}: {url} (reason: {reason})")
    return RESULT_OK


def add_sync_workflow(repo_name, branch, _username=None, dry_run=False, force=False):
    """Add or update sync-fork.yml in a repo. Return True on success."""
    return _add_sync_workflow_result(repo_name, branch, dry_run, force) == RESULT_OK


def _is_not_found_error(error_payload):
    """Return True when an error payload/message indicates HTTP 404."""
    if isinstance(error_payload, dict):
        if (
            error_payload.get("error_type") == "not_found"
            or error_payload.get("error_code") == "E_GH_NOT_FOUND"
            or error_payload.get("status_code") == 404
        ):
            return True
        error_message = error_payload.get("error")
    else:
        error_message = error_payload

    lowered = (error_message or "").strip().lower()
    return (
        lowered == "404"
        or "not found" in lowered
        or "http 404" in lowered
        or "(404)" in lowered
    )


def _create_restore_point(fork_name, fork_branch):
    """Create or update backup/pre-sync to the current fork branch head."""
    backup_ref = "backup/pre-sync"
    head_ref = gh_api(f"repos/{fork_name}/git/ref/heads/{fork_branch}")
    if not isinstance(head_ref, dict) or "error" in head_ref:
        print("  ABORT: Cannot read current fork head for restore point.")
        print(f"  Detail: {_short_error(head_ref)}")
        return False

    head_sha = (head_ref.get("object") or {}).get("sha")
    if not head_sha:
        print("  ABORT: Cannot resolve current fork head SHA for restore point.")
        return False

    update_result = gh_api(
        f"repos/{fork_name}/git/refs/heads/{backup_ref}",
        method="PATCH",
        payload={"sha": head_sha, "force": True},
    )
    if isinstance(update_result, dict) and "error" not in update_result:
        print(f"  Restore point updated: {backup_ref} -> {head_sha[:12]}")
        return True

    if not isinstance(update_result, dict):
        print("  ABORT: Cannot update restore point branch.")
        print(f"  Detail: {_short_error(update_result)}")
        return False

    if not _is_not_found_error(update_result):
        print("  ABORT: Cannot update restore point branch.")
        print(f"  Detail: {_short_error(update_result)}")
        return False

    create_result = gh_api(
        f"repos/{fork_name}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{backup_ref}", "sha": head_sha},
    )
    if not isinstance(create_result, dict) or "error" in create_result:
        print("  ABORT: Cannot create restore point branch.")
        print(f"  Detail: {_short_error(create_result)}")
        return False

    print(f"  Restore point created: {backup_ref} -> {head_sha[:12]}")
    return True


def _sync_now_result(fork_name, fork_branch, upstream_name, upstream_branch,
                     dry_run=False, force=False):
    """Perform a one-time sync with safety check. Return operation result."""
    if dry_run:
        print(
            f"  [DRY-RUN] Would create/refresh backup/pre-sync and "
            f"sync {upstream_name} -> {fork_name}"
        )
        return RESULT_OK

    safety_result = safety_check(
        fork_name, fork_branch, upstream_name, upstream_branch, force
    )
    if safety_result is SKIP:
        return RESULT_OK
    if safety_result is ABORT:
        return RESULT_SAFETY

    if not _create_restore_point(fork_name, fork_branch):
        return RESULT_SAFETY

    result = gh_api(
        f"repos/{fork_name}/merge-upstream",
        method="POST",
        payload={"branch": fork_branch},
    )

    if not isinstance(result, dict):
        print("  FAIL: Invalid merge-upstream response.")
        return RESULT_ERROR

    if "error" in result:
        err = result["error"].lower()
        if "already up to date" in err or "no new commits" in err:
            print("  Already up to date.")
            return RESULT_OK
        print(f"  FAIL: {result['error'][:200]}")
        return RESULT_ERROR

    print(f"  Synced: {result.get('message', 'ok')}")
    return RESULT_OK


def sync_now(fork_name, fork_branch, upstream_name, upstream_branch,
             dry_run=False, force=False):
    """Perform a one-time sync with safety check. Return True on success."""
    return (
        _sync_now_result(
            fork_name, fork_branch, upstream_name, upstream_branch,
            dry_run, force,
        ) == RESULT_OK
    )


def _resolve_specific_forks(username, repo_names):
    """Resolve explicitly requested repos. Return (forks, had_errors)."""
    forks = []
    had_errors = False
    for name in repo_names:
        full = name if "/" in name else f"{username}/{name}"
        repo = gh_api(f"repos/{full}")
        if isinstance(repo, dict) and "error" in repo:
            print(f"Error: {name} - {_short_error(repo)[:100]}")
            had_errors = True
            continue
        if not isinstance(repo, dict):
            print(f"Error: {name} - invalid repository response.")
            had_errors = True
            continue

        fork = build_fork_record(repo)
        if not fork:
            print(f"Error: {name} - target repo is not a fork.")
            had_errors = True
            continue
        forks.append(fork)
    return forks, had_errors


def _format_auth_summary(auth_info):
    """Return a short auth mode label for text and JSON summaries."""
    mode = auth_info.get("mode")
    source = auth_info.get("source")
    if source:
        return f"{mode} ({source})"
    return mode or "unknown"


def _split_log_lines(output):
    """Normalize captured stdout into a list of log lines."""
    return [line for line in output.splitlines() if line.strip()]


def _run_with_captured_stdout(func, *args):
    """Run a function while capturing stdout for optional JSON output."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = func(*args)
    return result, buffer.getvalue()


def _parse_cli_args(argv):
    """Parse CLI flags without changing the existing command contract."""
    parsed = {
        "dry_run": False,
        "force": False,
        "add_mode": False,
        "verify_schedule": False,
        "json_output": "--json" in argv,
        "specific_repos": [],
        "show_help": False,
        "error": None,
    }

    for arg in argv:
        if arg in {"-h", "--help"}:
            parsed["show_help"] = True
            return parsed

        if arg.startswith("-"):
            if arg == "--dry-run":
                parsed["dry_run"] = True
                continue
            if arg == "--force":
                parsed["force"] = True
                continue
            if arg == "--add-workflow":
                parsed["add_mode"] = True
                continue
            if arg == "--verify-schedule":
                parsed["verify_schedule"] = True
                continue
            if arg == "--json":
                parsed["json_output"] = True
                continue

            parsed["error"] = f"Unknown option: {arg}"
            return parsed

        parsed["specific_repos"].append(arg)

    return parsed


def _dedupe_preserve_order(items):
    """Return unique values from items while preserving first-seen order."""
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _print_help():
    """Print the sync-forks CLI help text."""
    print(HELP_TEXT.rstrip())


def _build_verify_schedule_row(fork):
    """Collect schedule-readiness details for one fork repository."""
    full_name = fork["full_name"]
    branch = fork["branch"]
    encoded_branch = encode_url_component(branch)

    row = {
        "name": fork["name"],
        "full_name": full_name,
        "branch": branch,
        "upstream": fork["parent"],
        "upstream_branch": fork["parent_branch"],
        "workflow_installed": False,
        "workflow_current": False,
        "workflow_update_reason": None,
        "secret_sync_pat_present": False,
        "last_scheduled_run": None,
        "result": RESULT_OK,
    }

    workflow = gh_api(
        "repos/"
        f"{full_name}/contents/.github/workflows/sync-fork.yml?ref={encoded_branch}"
    )
    if isinstance(workflow, dict) and "error" not in workflow:
        row["workflow_installed"] = True
        needs_update, reason = _needs_workflow_update(workflow)
        row["workflow_current"] = not needs_update
        if needs_update:
            row["workflow_update_reason"] = reason
    elif not _is_not_found_error(workflow):
        row["result"] = RESULT_ERROR
        row["workflow_error"] = _short_error(workflow)
    else:
        row["workflow_update_reason"] = "missing file"

    secret = gh_api(f"repos/{full_name}/actions/secrets/SYNC_PAT")
    if isinstance(secret, dict) and "error" not in secret:
        row["secret_sync_pat_present"] = True
    elif not _is_not_found_error(secret):
        row["result"] = RESULT_ERROR
        row["secret_error"] = _short_error(secret)

    scheduled_runs = gh_api(
        "repos/"
        f"{full_name}/actions/workflows/sync-fork.yml/runs?event=schedule&per_page=1"
    )
    if isinstance(scheduled_runs, dict) and "error" not in scheduled_runs:
        runs = scheduled_runs.get("workflow_runs")
        if isinstance(runs, list) and runs:
            last = runs[0]
            if isinstance(last, dict):
                row["last_scheduled_run"] = {
                    "status": last.get("status"),
                    "conclusion": last.get("conclusion"),
                    "created_at": last.get("created_at"),
                    "updated_at": last.get("updated_at"),
                    "html_url": last.get("html_url"),
                }
    elif not _is_not_found_error(scheduled_runs):
        row["result"] = RESULT_ERROR
        row["scheduled_run_error"] = _short_error(scheduled_runs)

    if row["result"] == RESULT_ERROR:
        row["next_action"] = "investigate_api_errors"
    elif not row["workflow_installed"]:
        row["next_action"] = "install_sync_workflow"
    elif not row["workflow_current"]:
        row["next_action"] = "update_sync_workflow"
    elif not row["secret_sync_pat_present"]:
        row["next_action"] = "set_sync_pat_secret"
    elif row["last_scheduled_run"] is None:
        row["next_action"] = "wait_or_trigger_smoke_run"
    elif row["last_scheduled_run"].get("conclusion") != "success":
        row["next_action"] = "inspect_last_scheduled_failure"
    else:
        row["next_action"] = "none"

    return row


def _verify_schedule_readiness(forks):
    """Return readiness rows and summary counts for schedule verification mode."""
    rows = []
    summary = {
        "total": len(forks),
        "ready": 0,
        "missing_workflow": 0,
        "workflow_needs_update": 0,
        "missing_secret": 0,
        "scheduled_run_missing_or_unhealthy": 0,
        "errors": 0,
    }

    for fork in forks:
        row = _build_verify_schedule_row(fork)
        rows.append(row)

        if row["result"] == RESULT_ERROR:
            summary["errors"] += 1
            continue
        if not row["workflow_installed"]:
            summary["missing_workflow"] += 1
        elif not row["workflow_current"]:
            summary["workflow_needs_update"] += 1
        if not row["secret_sync_pat_present"]:
            summary["missing_secret"] += 1
        run = row["last_scheduled_run"] or {}
        if run.get("conclusion") != "success":
            summary["scheduled_run_missing_or_unhealthy"] += 1
        if row["next_action"] == "none":
            summary["ready"] += 1

    return rows, summary


def _run(parsed):
    """Execute the sync-forks workflow and return (exit_code, report)."""
    dry_run = parsed.get("dry_run", False)
    force = parsed.get("force", False)
    add_mode = parsed.get("add_mode", False)
    verify_schedule = parsed.get("verify_schedule", False)
    specific_repos = parsed.get("specific_repos", [])
    unique_specific_repos = _dedupe_preserve_order(specific_repos)
    json_output = parsed.get("json_output", False)

    check_gh_cli()
    auth_context = get_auth_context()
    auth_info = auth_context["auth"]
    username = auth_context["username"]

    mode = "verify-schedule" if verify_schedule else (
        "add-workflow" if add_mode else "sync"
    )
    report = {
        "tool": "sync-forks",
        "user": username,
        "auth": auth_info,
        "mode": mode,
        "dry_run": dry_run,
        "force": force,
        "discovery": None,
        "repos": [],
        "summary": {
            "requested": len(specific_repos) if specific_repos else None,
            "matched": 0,
            "success": 0,
            "failed": 0,
            "safety_abort": 0,
            "resolve_errors": 0,
        },
    }

    force_ack = os.environ.get(FORCE_SYNC_ACK_ENV, "").strip()
    if (
        force and not dry_run and not add_mode and not verify_schedule
        and force_ack != FORCE_SYNC_ACK_VALUE
    ):
        report.update({
            "error": "force_confirmation_required",
            "error_code": "E_FORCE_CONFIRMATION_REQUIRED",
            "error_type": "safety",
            "message": (
                "Refusing --force sync without explicit confirmation. "
                f"Set {FORCE_SYNC_ACK_ENV}={FORCE_SYNC_ACK_VALUE} and retry."
            ),
        })
        if not json_output:
            print(f"ERROR: {report['message']}")
        return EXIT_ERROR, report

    text_mode = "VERIFY-SCHEDULE" if verify_schedule else (
        "ADD-WORKFLOW" if add_mode else "SYNC"
    )
    if dry_run:
        text_mode += " (DRY-RUN)"
    if force:
        text_mode += " (FORCE)"

    if not json_output:
        print(f"GitHub user: {username}")
        print(f"Auth: {_format_auth_summary(auth_info)}")
        print(f"Mode: {text_mode}")
        print()

    if specific_repos:
        report["discovery"] = {
            "source": "explicit_args",
            "requested": unique_specific_repos,
        }
        if json_output:
            resolve_result, resolve_output = _run_with_captured_stdout(
                _resolve_specific_forks, username, unique_specific_repos,
            )
            forks, resolve_errors = resolve_result
            resolve_logs = _split_log_lines(resolve_output)
            if resolve_logs:
                report["resolve_logs"] = resolve_logs
        else:
            forks, resolve_errors = _resolve_specific_forks(
                username,
                unique_specific_repos,
            )
    else:
        forks, discovery = get_fork_repos_with_evidence(username)
        report["discovery"] = discovery
        resolve_errors = False

    report["summary"]["resolve_errors"] = int(resolve_errors)
    report["summary"]["matched"] = len(forks)

    if not forks:
        message = (
            "No valid fork repos found."
            if specific_repos else
            "No fork repos found."
        )
        report["message"] = message
        if not json_output:
            print(message)
        exit_code = EXIT_ERROR if (specific_repos or resolve_errors) else EXIT_OK
        return exit_code, report

    if verify_schedule:
        rows, verify_summary = _verify_schedule_readiness(forks)
        report["repos"] = rows
        report["summary"].update(verify_summary)
        report["message"] = (
            "Schedule readiness verified: "
            f"ready {verify_summary['ready']}/{verify_summary['total']}."
        )
        if not json_output:
            print(report["message"])
        if verify_summary["errors"]:
            return EXIT_ERROR, report
        if (
            verify_summary["missing_workflow"]
            or verify_summary["missing_secret"]
            or verify_summary["scheduled_run_missing_or_unhealthy"]
        ):
            return EXIT_SAFETY, report
        return EXIT_OK, report

    if not json_output:
        print(f"Found {len(forks)} fork repo(s):\n")
        for fork in forks:
            print(f"  {fork['full_name']}")
            print(f"    branch: {fork['branch']}, upstream: {fork['parent']}")
        print()

    for fork in forks:
        if not json_output:
            print(f"[{fork['name']}]")

        operation = _add_sync_workflow_result if add_mode else _sync_now_result
        operation_args = (
            (fork["full_name"], fork["branch"], dry_run, force)
            if add_mode else
            (
                fork["full_name"], fork["branch"],
                fork["parent"], fork["parent_branch"],
                dry_run, force,
            )
        )
        result, output = _run_with_captured_stdout(operation, *operation_args)
        if not json_output and output:
            print(output, end="")

        repo_report = {
            "name": fork["name"],
            "full_name": fork["full_name"],
            "branch": fork["branch"],
            "upstream": fork["parent"],
            "upstream_branch": fork["parent_branch"],
            "result": result,
        }
        if json_output:
            repo_report["logs"] = _split_log_lines(output)
        report["repos"].append(repo_report)

        if result == RESULT_OK:
            report["summary"]["success"] += 1
        elif result == RESULT_SAFETY:
            report["summary"]["safety_abort"] += 1
        else:
            report["summary"]["failed"] += 1

        if not json_output:
            print()

    action = "updated" if add_mode else "synced"
    report["message"] = (
        f"Done: {report['summary']['success']}/{len(forks)} repos {action}."
    )
    if not json_output:
        print(report["message"])

    if report["summary"]["failed"] or resolve_errors:
        return EXIT_ERROR, report
    if report["summary"]["safety_abort"]:
        return EXIT_SAFETY, report
    return EXIT_OK, report


# ── CLI ───────────────────────────────────────────────────────

def main():
    parsed = _parse_cli_args(sys.argv[1:])
    if parsed["show_help"]:
        _print_help()
        return EXIT_OK
    if parsed["error"]:
        if parsed["json_output"]:
            print(json.dumps({
                "tool": "sync-forks",
                "error": parsed["error"],
                "error_code": "E_CLI_UNKNOWN_OPTION",
                "error_type": "cli",
            }, ensure_ascii=False, indent=2))
            return EXIT_ERROR
        print(f"Error: {parsed['error']}")
        print()
        _print_help()
        return EXIT_ERROR
    try:
        exit_code, report = _run(parsed)
    except (AuthContextError, ForkDiscoveryError) as exc:
        if parsed["json_output"]:
            print(json.dumps(exc.to_dict("sync-forks"), ensure_ascii=False, indent=2))
            return EXIT_ERROR
        print(f"ERROR: {exc.message}")
        return EXIT_ERROR
    if parsed["json_output"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
