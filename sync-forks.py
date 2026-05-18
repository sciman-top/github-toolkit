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
MODE_SYNC = "sync"
MODE_ADD_WORKFLOW = "add-workflow"
MODE_VERIFY_SCHEDULE = "verify-schedule"
MODE_VERIFY_RELEASES = "verify-releases"
MODE_OPTION_FLAGS = (
    ("add_mode", "--add-workflow"),
    ("verify_schedule", "--verify-schedule"),
    ("verify_releases", "--verify-releases"),
)
VERIFY_SCHEDULE_SAFETY_SUMMARY_KEYS = (
    "missing_workflow",
    "workflow_needs_update",
    "missing_secret",
    "scheduled_run_missing_or_unhealthy",
)
VERIFY_RELEASE_SAFETY_SUMMARY_KEYS = (
    "latest_stable_missing_or_without_assets",
    "old_stable_assets_present",
)
FORCE_SYNC_ACK_ENV = "SYNC_FORKS_FORCE_ACK"
FORCE_SYNC_ACK_VALUE = "I_UNDERSTAND"
STABLE_SOURCE_ONLY_REPOS = ("sciman-top/cockpit-tools-local",)
STABLE_SOURCE_ONLY_REPOS_TEXT = ",".join(STABLE_SOURCE_ONLY_REPOS)
STABLE_SOURCE_ONLY_REPO_SET = {
    repo.lower()
    for repo in STABLE_SOURCE_ONLY_REPOS
}
INSTALLABLE_RELEASE_ASSET_RE = re.compile(
    r"\.(?:exe|msi|deb|rpm|appimage|dmg|pkg|zip|tar\.gz)$",
    re.IGNORECASE,
)
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
  --verify-releases Verify stable release mirror retention policy
  --dry-run        Preview planned changes without modifying repos
  --force          Bypass safety checks or force workflow rewrite
                   (real writes need explicit confirmation)
  --json           Emit a machine-readable JSON summary
  -h, --help       Show this help message

Force confirmation for real --force writes:
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
        uses: actions/checkout@v6
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

      - name: Stable source policy guard
        id: stable_source_policy
        if: steps.upstream.outputs.skip != 'true'
        env:
          STABLE_SOURCE_ONLY_REPOS: "{stable_source_only_repos}"
        run: |
          set -euo pipefail
          IFS=',' read -ra REPOS <<< "$STABLE_SOURCE_ONLY_REPOS"
          for repo in "${{REPOS[@]}}"; do
            repo=$(echo "$repo" | xargs)
            if [ "$repo" = "${{{{ github.repository }}}}" ]; then
              echo "::notice::Stable-source-only policy is enabled for $repo. Default-branch merge-upstream is skipped; mirror stable releases/tags instead."
              echo "skip=true" >> "$GITHUB_OUTPUT"
              exit 0
            fi
          done
          echo "skip=false" >> "$GITHUB_OUTPUT"

      - name: Safety check - compare file counts
        if: steps.upstream.outputs.skip != 'true' && steps.stable_source_policy.outputs.skip != 'true'
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
        if: steps.upstream.outputs.skip != 'true' && steps.stable_source_policy.outputs.skip != 'true'
        run: |
          set -euo pipefail
          BACKUP_BRANCH="backup/pre-sync"
          git branch -f "$BACKUP_BRANCH" HEAD
          git push origin "$BACKUP_BRANCH" --force

      - name: Diff guard - inspect deletions
        if: steps.upstream.outputs.skip != 'true' && steps.stable_source_policy.outputs.skip != 'true'
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
        if: steps.upstream.outputs.skip != 'true' && steps.stable_source_policy.outputs.skip != 'true'
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


def _render_workflow_template(cron=SYNC_CRON):
    """Render the managed fork-sync workflow template."""
    return WORKFLOW_TEMPLATE.format(
        cron=cron,
        stable_source_only_repos=STABLE_SOURCE_ONLY_REPOS_TEXT,
    )


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
        "actions/checkout@v6",
        "Resolve upstream",
        "Stable source policy guard",
        "Stable-source-only policy is enabled",
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


def _is_stable_source_only_repo(full_name):
    """Return True when normal default-branch sync should be skipped."""
    return str(full_name or "").lower() in STABLE_SOURCE_ONLY_REPO_SET


def _source_update_policy(full_name):
    """Return the declared update policy for one fork full_name."""
    if _is_stable_source_only_repo(full_name):
        return "stable_release_only"
    return "upstream_default_branch"


def _base_fork_row(fork):
    """Return common report fields for fork-level verification rows."""
    full_name = fork["full_name"]
    return {
        "name": fork["name"],
        "full_name": full_name,
        "upstream": fork["parent"],
        "source_update_policy": _source_update_policy(full_name),
    }


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

    content = _render_workflow_template()
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
    if _is_stable_source_only_repo(fork_name) and not force:
        print(
            "  SKIP: stable-source-only policy is enabled. "
            "Default-branch merge-upstream is skipped; mirror stable "
            "releases/tags instead."
        )
        return RESULT_OK

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
        "verify_releases": False,
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
            if arg == "--verify-releases":
                parsed["verify_releases"] = True
                continue
            if arg == "--json":
                parsed["json_output"] = True
                continue

            parsed["error"] = f"Unknown option: {arg}"
            return parsed

        parsed["specific_repos"].append(arg)

    selected_modes = _selected_mode_flags(parsed)
    if len(selected_modes) > 1:
        options = ", ".join(option for _name, option in MODE_OPTION_FLAGS)
        parsed["error"] = (
            f"Choose only one mode: {options}"
        )

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


def _stable_releases(releases):
    """Return non-draft, non-prerelease releases from a GitHub payload."""
    if not isinstance(releases, list):
        return []
    return [
        release for release in releases
        if isinstance(release, dict)
        and not release.get("draft")
        and not release.get("prerelease")
    ]


def _asset_names(release):
    """Return asset names from one release payload."""
    if not isinstance(release, dict):
        return []
    assets = release.get("assets")
    if not isinstance(assets, list):
        return []
    return [
        asset.get("name")
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    ]


def _asset_count(release):
    """Return the number of assets on one release payload."""
    if not isinstance(release, dict):
        return 0
    assets = release.get("assets")
    return len(assets) if isinstance(assets, list) else 0


def _installable_asset_count(release):
    """Return installable/package asset count for one release."""
    return sum(
        1 for name in _asset_names(release)
        if INSTALLABLE_RELEASE_ASSET_RE.search(name)
    )


def _find_release_by_tag(releases, tag_name):
    """Find one release by tag name."""
    for release in releases:
        if isinstance(release, dict) and release.get("tag_name") == tag_name:
            return release
    return None


def _release_retention_next_action(
    latest_installable_asset_count,
    latest_fork_release,
    fork_latest_asset_count,
    old_stable_asset_count,
):
    """Return release-retention next action from normalized counters."""
    if latest_installable_asset_count <= 0:
        return "none"
    if latest_fork_release is None:
        return "mirror_latest_stable_release"
    if fork_latest_asset_count <= 0:
        return "mirror_latest_stable_assets"
    if old_stable_asset_count > 0:
        return "prune_old_stable_release_assets"
    return "none"


def _latest_workflow_run(runs_payload):
    """Return normalized latest workflow run details, or None if absent/malformed."""
    if not isinstance(runs_payload, dict) or "error" in runs_payload:
        return None
    runs = runs_payload.get("workflow_runs")
    if not isinstance(runs, list) or not runs:
        return None
    last = runs[0]
    if not isinstance(last, dict):
        return None
    return {
        "status": last.get("status"),
        "conclusion": last.get("conclusion"),
        "created_at": last.get("created_at"),
        "updated_at": last.get("updated_at"),
        "html_url": last.get("html_url"),
    }


def _fetch_latest_workflow_run(full_name, event):
    """Fetch latest sync-fork workflow run for one event type."""
    return gh_api(
        "repos/"
        f"{full_name}/actions/workflows/sync-fork.yml/runs"
        f"?event={event}&per_page=1"
    )


def _schedule_next_action(row):
    """Return schedule-readiness next action from one normalized row."""
    if row["result"] == RESULT_ERROR:
        return "investigate_api_errors"
    if not row["workflow_installed"]:
        return "install_sync_workflow"
    if not row["workflow_current"]:
        return "update_sync_workflow"
    if not row["secret_sync_pat_present"]:
        return "set_sync_pat_secret"
    if row["last_scheduled_run"] is None:
        smoke_run = row["last_smoke_run"] or {}
        if smoke_run.get("conclusion") == "success":
            return "none"
        return "wait_or_trigger_smoke_run"
    if row["last_scheduled_run"].get("conclusion") != "success":
        return "inspect_last_scheduled_failure"
    return "none"


def _build_verify_releases_row(fork):
    """Collect stable-release mirror retention details for one fork."""
    full_name = fork["full_name"]
    upstream = fork["parent"]
    row = {
        **_base_fork_row(fork),
        "release_policy": "latest_stable_assets_only",
        "latest_upstream_stable": None,
        "latest_upstream_asset_count": 0,
        "latest_upstream_installable_asset_count": 0,
        "fork_release_count": 0,
        "fork_latest_stable_asset_count": 0,
        "old_stable_release_asset_count": 0,
        "result": RESULT_OK,
    }

    upstream_releases = gh_api(f"repos/{upstream}/releases?per_page=100")
    fork_releases = gh_api(f"repos/{full_name}/releases?per_page=100")
    if not isinstance(upstream_releases, list):
        row["result"] = RESULT_ERROR
        row["release_error"] = _short_error(upstream_releases)
        row["next_action"] = "investigate_release_api_errors"
        return row
    if not isinstance(fork_releases, list):
        row["result"] = RESULT_ERROR
        row["release_error"] = _short_error(fork_releases)
        row["next_action"] = "investigate_release_api_errors"
        return row

    upstream_stable = _stable_releases(upstream_releases)
    fork_stable = _stable_releases(fork_releases)
    row["fork_release_count"] = len(fork_releases)
    if not upstream_stable:
        row["next_action"] = "none"
        return row

    latest_upstream = upstream_stable[0]
    latest_tag = latest_upstream.get("tag_name")
    row["latest_upstream_stable"] = latest_tag
    row["latest_upstream_asset_count"] = _asset_count(latest_upstream)
    row["latest_upstream_installable_asset_count"] = (
        _installable_asset_count(latest_upstream)
    )

    latest_fork = _find_release_by_tag(fork_releases, latest_tag)
    row["fork_latest_stable_asset_count"] = _asset_count(latest_fork)
    row["old_stable_release_asset_count"] = sum(
        _asset_count(release)
        for release in fork_stable
        if release.get("tag_name") != latest_tag
    )

    row["next_action"] = _release_retention_next_action(
        row["latest_upstream_installable_asset_count"],
        latest_fork,
        row["fork_latest_stable_asset_count"],
        row["old_stable_release_asset_count"],
    )
    if row["latest_upstream_installable_asset_count"] <= 0:
        row["release_policy"] = "source_only_release_notes"

    return row


def _verify_release_retention(forks):
    """Return release-retention rows and summary counts."""
    rows = []
    summary = {
        "total": len(forks),
        "ready": 0,
        "latest_stable_assets_required": 0,
        "latest_stable_missing_or_without_assets": 0,
        "old_stable_assets_present": 0,
        "errors": 0,
    }
    for fork in forks:
        row = _build_verify_releases_row(fork)
        rows.append(row)
        if row["result"] == RESULT_ERROR:
            summary["errors"] += 1
            continue
        if row["latest_upstream_installable_asset_count"] > 0:
            summary["latest_stable_assets_required"] += 1
        if row["next_action"] in {
            "mirror_latest_stable_release",
            "mirror_latest_stable_assets",
        }:
            summary["latest_stable_missing_or_without_assets"] += 1
        if row["old_stable_release_asset_count"] > 0:
            summary["old_stable_assets_present"] += 1
        if row["next_action"] == "none":
            summary["ready"] += 1
    return rows, summary


def _build_verify_schedule_row(fork):
    """Collect schedule-readiness details for one fork repository."""
    full_name = fork["full_name"]
    branch = fork["branch"]
    encoded_branch = encode_url_component(branch)

    row = {
        **_base_fork_row(fork),
        "branch": branch,
        "upstream_branch": fork["parent_branch"],
        "workflow_installed": False,
        "workflow_current": False,
        "workflow_update_reason": None,
        "secret_sync_pat_present": False,
        "last_scheduled_run": None,
        "last_smoke_run": None,
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

    scheduled_runs = _fetch_latest_workflow_run(full_name, "schedule")
    if isinstance(scheduled_runs, dict) and "error" not in scheduled_runs:
        row["last_scheduled_run"] = _latest_workflow_run(scheduled_runs)
    elif not _is_not_found_error(scheduled_runs):
        row["result"] = RESULT_ERROR
        row["scheduled_run_error"] = _short_error(scheduled_runs)

    if (
        row["result"] == RESULT_OK
        and row["workflow_installed"]
        and row["workflow_current"]
        and row["secret_sync_pat_present"]
        and row["last_scheduled_run"] is None
    ):
        smoke_runs = _fetch_latest_workflow_run(full_name, "workflow_dispatch")
        if isinstance(smoke_runs, dict) and "error" not in smoke_runs:
            row["last_smoke_run"] = _latest_workflow_run(smoke_runs)
        elif not _is_not_found_error(smoke_runs):
            row["result"] = RESULT_ERROR
            row["smoke_run_error"] = _short_error(smoke_runs)

    row["next_action"] = _schedule_next_action(row)

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
        "stable_source_only": 0,
        "errors": 0,
    }

    for fork in forks:
        row = _build_verify_schedule_row(fork)
        rows.append(row)

        if row["result"] == RESULT_ERROR:
            summary["errors"] += 1
            continue
        if row["source_update_policy"] == "stable_release_only":
            summary["stable_source_only"] += 1
        if not row["workflow_installed"]:
            summary["missing_workflow"] += 1
        elif not row["workflow_current"]:
            summary["workflow_needs_update"] += 1
        if not row["secret_sync_pat_present"]:
            summary["missing_secret"] += 1
        run = row["last_scheduled_run"] or {}
        smoke_run = row["last_smoke_run"] or {}
        if (
            run.get("conclusion") != "success"
            and smoke_run.get("conclusion") != "success"
        ):
            summary["scheduled_run_missing_or_unhealthy"] += 1
        if row["next_action"] == "none":
            summary["ready"] += 1

    return rows, summary


def _mode_name(add_mode, verify_schedule, verify_releases=False):
    """Return the machine-readable execution mode name."""
    if verify_schedule:
        return MODE_VERIFY_SCHEDULE
    if verify_releases:
        return MODE_VERIFY_RELEASES
    if add_mode:
        return MODE_ADD_WORKFLOW
    return MODE_SYNC


def _selected_mode_flags(parsed):
    """Return selected mutually exclusive mode flag names."""
    return [
        name for name, _option in MODE_OPTION_FLAGS
        if parsed.get(name)
    ]


def _mode_writes_remotely(mode, dry_run):
    """Return True when the selected mode can perform remote writes."""
    if dry_run:
        return False
    if mode in {MODE_SYNC, MODE_ADD_WORKFLOW}:
        return True
    if mode in {MODE_VERIFY_SCHEDULE, MODE_VERIFY_RELEASES}:
        return False
    raise ValueError(f"Unclassified execution mode: {mode}")


def _text_mode_name(mode, dry_run, force):
    """Return the human-readable mode banner."""
    text_mode = mode.upper()
    if dry_run:
        text_mode += " (DRY-RUN)"
    if force:
        text_mode += " (FORCE)"
    return text_mode


def _build_run_report(username, auth_info, mode, dry_run, force, specific_repos):
    """Build the common report envelope for every sync-forks mode."""
    return {
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


def _force_confirmation_missing(force, mode, dry_run):
    """Return True when a real force write lacks explicit confirmation."""
    force_ack = os.environ.get(FORCE_SYNC_ACK_ENV, "").strip()
    return (
        force and _mode_writes_remotely(mode, dry_run)
        and force_ack != FORCE_SYNC_ACK_VALUE
    )


def _reject_unconfirmed_force_write(report, json_output):
    """Populate and optionally print the force-confirmation failure."""
    report.update({
        "error": "force_confirmation_required",
        "error_code": "E_FORCE_CONFIRMATION_REQUIRED",
        "error_type": "safety",
        "message": (
            "Refusing real --force write without explicit confirmation. "
            f"Set {FORCE_SYNC_ACK_ENV}={FORCE_SYNC_ACK_VALUE} and retry."
        ),
    })
    if not json_output:
        print(f"ERROR: {report['message']}")
    return EXIT_ERROR, report


def _summary_has_any(summary, keys):
    """Return True when any named summary counter is truthy."""
    return any(summary.get(key) for key in keys)


def _verification_exit_code(summary, safety_keys):
    """Map verification summary counters to the CLI exit contract."""
    if summary.get("errors"):
        return EXIT_ERROR
    if _summary_has_any(summary, safety_keys):
        return EXIT_SAFETY
    return EXIT_OK


def _print_run_header(username, auth_info, text_mode, json_output):
    """Print the text-mode run header."""
    if json_output:
        return
    print(f"GitHub user: {username}")
    print(f"Auth: {_format_auth_summary(auth_info)}")
    print(f"Mode: {text_mode}")
    print()


def _resolve_run_forks(username, specific_repos, json_output, report):
    """Resolve the fork list for explicit repo args or live discovery."""
    unique_specific_repos = _dedupe_preserve_order(specific_repos)
    if not specific_repos:
        forks, discovery = get_fork_repos_with_evidence(username)
        report["discovery"] = discovery
        return forks, False

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
        return forks, resolve_errors

    return _resolve_specific_forks(username, unique_specific_repos)


def _finish_empty_forks(report, specific_repos, resolve_errors, json_output):
    """Return the no-forks exit result while preserving CLI contract."""
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


def _run_verify_schedule_mode(report, forks, json_output):
    """Execute verify-schedule mode and return its exit result."""
    rows, verify_summary = _verify_schedule_readiness(forks)
    report["repos"] = rows
    report["summary"].update(verify_summary)
    report["message"] = (
        "Schedule readiness verified: "
        f"ready {verify_summary['ready']}/{verify_summary['total']}."
    )
    if not json_output:
        _print_verify_schedule_rows(rows)
        print(report["message"])
    return (
        _verification_exit_code(
            verify_summary,
            VERIFY_SCHEDULE_SAFETY_SUMMARY_KEYS,
        ),
        report,
    )


def _run_verify_releases_mode(report, forks, json_output):
    """Execute verify-releases mode and return its exit result."""
    rows, release_summary = _verify_release_retention(forks)
    report["repos"] = rows
    report["summary"].update(release_summary)
    report["message"] = (
        "Release retention verified: "
        f"ready {release_summary['ready']}/{release_summary['total']}."
    )
    if not json_output:
        _print_verify_release_rows(rows)
        print(report["message"])
    return (
        _verification_exit_code(
            release_summary,
            VERIFY_RELEASE_SAFETY_SUMMARY_KEYS,
        ),
        report,
    )


def _format_bool_status(value):
    """Return a compact status label for text tables."""
    return "yes" if value else "no"


def _format_last_scheduled_run(row):
    """Return a compact last scheduled run label for text tables."""
    run = row.get("last_scheduled_run")
    if not isinstance(run, dict):
        return "-"
    conclusion = run.get("conclusion") or run.get("status")
    return str(conclusion) if conclusion else "-"


def _print_verify_schedule_rows(rows):
    """Print per-repo readiness rows for text-mode diagnostics."""
    if not rows:
        return
    print("Schedule readiness details:")
    print(
        f"{'Repository':<35} {'Workflow':<10} {'Current':<8} "
        f"{'Secret':<7} {'Source policy':<23} {'Last':<10} {'Next action'}"
    )
    print("-" * 118)
    for row in rows:
        print(
            f"{row['full_name']:<35} "
            f"{_format_bool_status(row.get('workflow_installed')):<10} "
            f"{_format_bool_status(row.get('workflow_current')):<8} "
            f"{_format_bool_status(row.get('secret_sync_pat_present')):<7} "
            f"{row.get('source_update_policy') or '-':<23} "
            f"{_format_last_scheduled_run(row):<10} "
            f"{row.get('next_action') or '-'}"
        )
    print()


def _print_verify_release_rows(rows):
    """Print per-repo release-retention rows for text-mode diagnostics."""
    if not rows:
        return
    print("Release retention details:")
    print(
        f"{'Repository':<35} {'Latest stable':<15} {'Upstream assets':<15} "
        f"{'Fork latest':<12} {'Old assets':<10} {'Next action'}"
    )
    print("-" * 112)
    for row in rows:
        print(
            f"{row['full_name']:<35} "
            f"{str(row.get('latest_upstream_stable') or '-'):<15} "
            f"{row.get('latest_upstream_asset_count', 0):<15} "
            f"{row.get('fork_latest_stable_asset_count', 0):<12} "
            f"{row.get('old_stable_release_asset_count', 0):<10} "
            f"{row.get('next_action') or '-'}"
        )
    print()


def _print_fork_overview(forks, json_output):
    """Print the text-mode fork overview before mutating operations."""
    if json_output:
        return
    print(f"Found {len(forks)} fork repo(s):\n")
    for fork in forks:
        print(f"  {fork['full_name']}")
        print(f"    branch: {fork['branch']}, upstream: {fork['parent']}")
    print()


def _run_single_repo_operation(fork, mode, dry_run, force):
    """Run the selected operation for one fork and capture operation logs."""
    add_mode = mode == MODE_ADD_WORKFLOW
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
    return _run_with_captured_stdout(operation, *operation_args)


def _append_operation_report(report, fork, result, output, json_output):
    """Append one repo operation result and update summary counters."""
    repo_report = {
        "name": fork["name"],
        "full_name": fork["full_name"],
        "branch": fork["branch"],
        "upstream": fork["parent"],
        "upstream_branch": fork["parent_branch"],
        "source_update_policy": _source_update_policy(fork["full_name"]),
        "result": result,
    }
    if json_output:
        repo_report["logs"] = _split_log_lines(output)
    report["repos"].append(repo_report)

    _update_operation_summary(report["summary"], result)


def _update_operation_summary(summary, result):
    """Update operation summary counters for one repo result."""
    if result == RESULT_OK:
        summary["success"] += 1
    elif result == RESULT_SAFETY:
        summary["safety_abort"] += 1
    else:
        summary["failed"] += 1


def _run_mutating_or_dryrun_mode(report, forks, mode, dry_run, force,
                                 resolve_errors, json_output):
    """Run add-workflow or sync mode over all resolved forks."""
    _print_fork_overview(forks, json_output)

    for fork in forks:
        if not json_output:
            print(f"[{fork['name']}]")

        result, output = _run_single_repo_operation(
            fork, mode, dry_run, force,
        )
        if not json_output and output:
            print(output, end="")

        _append_operation_report(report, fork, result, output, json_output)

        if not json_output:
            print()

    action = "updated" if mode == MODE_ADD_WORKFLOW else "synced"
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


def _run(parsed):
    """Execute the sync-forks workflow and return (exit_code, report)."""
    dry_run = parsed.get("dry_run", False)
    force = parsed.get("force", False)
    add_mode = parsed.get("add_mode", False)
    verify_schedule = parsed.get("verify_schedule", False)
    verify_releases = parsed.get("verify_releases", False)
    specific_repos = parsed.get("specific_repos", [])
    json_output = parsed.get("json_output", False)

    check_gh_cli()
    auth_context = get_auth_context()
    auth_info = auth_context["auth"]
    username = auth_context["username"]

    mode = _mode_name(add_mode, verify_schedule, verify_releases)
    report = _build_run_report(
        username, auth_info, mode, dry_run, force, specific_repos,
    )

    if _force_confirmation_missing(force, mode, dry_run):
        return _reject_unconfirmed_force_write(report, json_output)

    _print_run_header(
        username,
        auth_info,
        _text_mode_name(mode, dry_run, force),
        json_output,
    )

    forks, resolve_errors = _resolve_run_forks(
        username, specific_repos, json_output, report,
    )
    report["summary"]["resolve_errors"] = int(resolve_errors)
    report["summary"]["matched"] = len(forks)

    if not forks:
        return _finish_empty_forks(
            report, specific_repos, resolve_errors, json_output,
        )

    if verify_schedule:
        return _run_verify_schedule_mode(report, forks, json_output)
    if verify_releases:
        return _run_verify_releases_mode(report, forks, json_output)

    return _run_mutating_or_dryrun_mode(
        report, forks, mode, dry_run, force, resolve_errors, json_output,
    )


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
