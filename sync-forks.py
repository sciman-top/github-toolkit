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
import sys

from gh_utils import (
    AuthContextError,
    DELETION_THRESHOLD,
    ForkDiscoveryError,
    SYNC_CRON,
    build_fork_record,
    check_gh_cli,
    get_compare_head,
    get_auth_context,
    get_file_count,
    get_fork_repos,
    get_fork_repos_with_evidence,
    gh_api,
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

HELP_TEXT = """\
Usage:
  python sync-forks.py [options] [repo-name ...]

Options:
  --add-workflow   Add or update sync-fork.yml instead of syncing now
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
        run: |
          set -euo pipefail
          FORK_FILES=$(git ls-tree -r --name-only HEAD | wc -l | tr -d '[:space:]')
          COMPARE_JSON=$(gh api "repos/${{{{ github.repository }}}}/compare/${{{{ github.event.repository.default_branch }}}}...${{{{ steps.upstream.outputs.owner }}}}:${{{{ steps.upstream.outputs.branch }}}}")
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
          gh api repos/${{{{ github.repository }}}}/merge-upstream -X POST -f branch=${{{{ steps.upstream.outputs.branch }}}}
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
    for line in workflow_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "workflow_dispatch" in stripped:
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
    if not compare_head:
        print("  ABORT: Upstream metadata is incomplete, cannot inspect diff safely.")
        return ABORT
    compare = gh_api(
        f"repos/{fork_name}/compare/{fork_branch}...{compare_head}"
    )
    if not isinstance(compare, dict) or "error" in compare:
        print("  ABORT: Cannot inspect upstream diff safely.")
        return ABORT

    total_deletions = int(compare.get("total_deletions", 0) or 0)
    deleted_files = [
        item.get("filename", "")
        for item in compare.get("files", [])
        if item.get("status") == "removed"
    ]
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
    existing = gh_api(
        f"repos/{repo_name}/contents/.github/workflows/sync-fork.yml?ref={branch}"
    )
    needs_update, reason = _needs_workflow_update(existing)
    has_existing = (
        isinstance(existing, dict)
        and "error" not in existing
        and bool(existing.get("sha"))
    )

    if has_existing and not needs_update and not force:
        print("  SKIP: sync-fork.yml already includes workflow_dispatch")
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

    if "error" in result:
        print(f"  FAIL: {result['error'][:200]}")
        return RESULT_ERROR

    url = result.get("content", {}).get("html_url", "ok")
    action = "Updated" if has_existing else "Added"
    print(f"  {action}: {url} (reason: {reason})")
    return RESULT_OK


def add_sync_workflow(repo_name, branch, _username=None, dry_run=False, force=False):
    """Add or update sync-fork.yml in a repo. Return True on success."""
    return _add_sync_workflow_result(repo_name, branch, dry_run, force) == RESULT_OK


def _is_not_found_error(error_message):
    """Return True when an error message indicates HTTP 404 / not found."""
    lowered = (error_message or "").lower()
    return "not found" in lowered or "http 404" in lowered or "(404)" in lowered


def _create_restore_point(fork_name, fork_branch):
    """Create or update backup/pre-sync to the current fork branch head."""
    backup_ref = "backup/pre-sync"
    head_ref = gh_api(f"repos/{fork_name}/git/ref/heads/{fork_branch}")
    if "error" in head_ref:
        print("  ABORT: Cannot read current fork head for restore point.")
        print(f"  Detail: {head_ref['error'][:200]}")
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
    if "error" not in update_result:
        print(f"  Restore point updated: {backup_ref} -> {head_sha[:12]}")
        return True

    if not _is_not_found_error(update_result.get("error")):
        print("  ABORT: Cannot update restore point branch.")
        print(f"  Detail: {update_result['error'][:200]}")
        return False

    create_result = gh_api(
        f"repos/{fork_name}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{backup_ref}", "sha": head_sha},
    )
    if "error" in create_result:
        print("  ABORT: Cannot create restore point branch.")
        print(f"  Detail: {create_result['error'][:200]}")
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
        if "error" in repo:
            print(f"Error: {name} — {repo['error'][:100]}")
            had_errors = True
            continue

        fork = build_fork_record(repo)
        if not fork:
            print(f"Error: {name} — target repo is not a fork.")
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
        "json_output": False,
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


def _run(parsed):
    """Execute the sync-forks workflow and return (exit_code, report)."""
    dry_run = parsed["dry_run"]
    force = parsed["force"]
    add_mode = parsed["add_mode"]
    specific_repos = parsed["specific_repos"]
    unique_specific_repos = _dedupe_preserve_order(specific_repos)
    json_output = parsed["json_output"]

    check_gh_cli()
    auth_context = get_auth_context()
    auth_info = auth_context["auth"]
    username = auth_context["username"]

    mode = "add-workflow" if add_mode else "sync"
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
    if force and not dry_run and not add_mode and force_ack != FORCE_SYNC_ACK_VALUE:
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

    text_mode = "ADD-WORKFLOW" if add_mode else "SYNC"
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
