#!/usr/bin/env python3
"""
sync-forks.py — Sync fork repos from upstream with safety checks.

Safety mechanism:
  Before syncing, compares file counts between fork and upstream.
  If upstream has significantly fewer files (suggesting deletion/wipe),
  the sync is ABORTED. Threshold: upstream < 50% of fork's file count.

NOTE: Authentication relies on gh CLI plaintext token (HTTPS).
      No passwords/tokens are handled in this code directly.
      See README.md "认证方式" section for details.

Usage:
  python sync-forks.py                  # Sync all forks now
  python sync-forks.py repo-name        # Sync specific repo (owner/name or just name)
  python sync-forks.py --add-workflow   # Add/update sync-fork.yml to repos
  python sync-forks.py --dry-run        # Preview only
  python sync-forks.py --force          # Skip safety checks (dangerous)
"""

import base64
import sys

from gh_utils import (
    DELETION_THRESHOLD,
    SYNC_CRON,
    check_gh_cli,
    get_current_user,
    get_file_count,
    get_fork_repos,
    gh_api,
)

# ── Exit codes ────────────────────────────────────────────────
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SAFETY = 3

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
          COMPARE_JSON=$(gh api "repos/${{{{ github.repository }}}}/compare/${{{{ github.event.repository.default_branch }}}}...${{{{ steps.upstream.outputs.full_name }}}}:${{{{ steps.upstream.outputs.branch }}}}")
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
    return "workflow_dispatch" in workflow_text


def _needs_workflow_update(existing_file):
    """Return (needs_update: bool, reason: str)."""
    if "error" in existing_file:
        return True, "missing file"

    workflow_text = _decode_workflow_content(existing_file)
    if workflow_text is None:
        return True, "cannot decode existing workflow"

    if not _has_workflow_dispatch(workflow_text):
        return True, "missing workflow_dispatch trigger"

    return False, "already has workflow_dispatch"

def safety_check(fork_name, fork_branch, upstream_name, upstream_branch,
                 force=False):
    """Compare file counts. Return True if safe to sync."""
    if force:
        print("  [FORCE] Safety check bypassed.")
        return True

    fork_count = get_file_count(fork_name, fork_branch)
    upstream_count = get_file_count(upstream_name, upstream_branch)

    if fork_count is None:
        print("  WARN: Cannot get fork file count, proceeding.")
        return True
    if upstream_count is None:
        print("  WARN: Upstream unavailable or deleted. Skipping sync to preserve fork.")
        return None

    print(f"  Files: fork={fork_count}, upstream={upstream_count}")

    if upstream_count < fork_count * DELETION_THRESHOLD:
        print("  *** SAFETY ABORT ***")
        print(f"  Upstream has {upstream_count} files, fork has {fork_count}.")
        print("  Upstream may have been wiped. Sync skipped for safety.")
        print("  Use --force to override (NOT recommended).")
        return False

    compare = gh_api(
        f"repos/{fork_name}/compare/{fork_branch}...{upstream_name}:{upstream_branch}"
    )
    if not isinstance(compare, dict) or "error" in compare:
        print("  ABORT: Cannot inspect upstream diff safely.")
        return False

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
        return False

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
        return False

    return True


def add_sync_workflow(repo_name, branch, username, dry_run=False, force=False):
    """Add or update sync-fork.yml in a repo. Return True on success."""
    existing = gh_api(
        f"repos/{repo_name}/contents/.github/workflows/sync-fork.yml?ref={branch}"
    )
    needs_update, reason = _needs_workflow_update(existing)
    has_existing = "error" not in existing

    if has_existing and not needs_update and not force:
        print("  SKIP: sync-fork.yml already includes workflow_dispatch")
        return True

    content = WORKFLOW_TEMPLATE.format(
        owner=username, cron=SYNC_CRON, threshold=DELETION_THRESHOLD,
    )
    encoded = base64.b64encode(content.encode()).decode()

    if dry_run:
        action = "update" if has_existing else "add"
        print(
            f"  [DRY-RUN] Would {action} sync-fork.yml "
            f"(branch: {branch}, reason: {reason})"
        )
        return True

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
        return False

    url = result.get("content", {}).get("html_url", "ok")
    action = "Updated" if has_existing else "Added"
    print(f"  {action}: {url} (reason: {reason})")
    return True


def sync_now(fork_name, fork_branch, upstream_name, upstream_branch,
             dry_run=False, force=False):
    """Perform a one-time sync with safety check. Return True on success."""
    if dry_run:
        print(f"  [DRY-RUN] Would sync {upstream_name} -> {fork_name}")
        return True

    safety_result = safety_check(
        fork_name, fork_branch, upstream_name, upstream_branch, force
    )
    if safety_result is None:
        return True
    if not safety_result:
        return False

    result = gh_api(
        f"repos/{fork_name}/merge-upstream",
        method="POST",
        payload={"branch": fork_branch},
    )

    if "error" in result:
        err = result["error"].lower()
        if "already up to date" in err or "no new commits" in err:
            print("  Already up to date.")
            return True
        print(f"  FAIL: {result['error'][:200]}")
        return False

    print(f"  Synced: {result.get('message', 'ok')}")
    return True


# ── CLI ───────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    add_mode = "--add-workflow" in args
    specific_repos = [a for a in args if not a.startswith("--")]

    check_gh_cli()
    username = get_current_user()

    mode = "ADD-WORKFLOW" if add_mode else "SYNC"
    if dry_run:
        mode += " (DRY-RUN)"
    if force:
        mode += " (FORCE)"
    print(f"GitHub user: {username}")
    print(f"Mode: {mode}")
    print()

    # ── Resolve target repos ──
    if specific_repos:
        forks = []
        for name in specific_repos:
            full = name if "/" in name else f"{username}/{name}"
            r = gh_api(f"repos/{full}")
            if "error" in r:
                print(f"Error: {name} — {r['error'][:100]}")
                continue
            parent_info = r.get("parent", {})
            forks.append({
                "name": r["name"],
                "full_name": r["full_name"],
                "branch": r["default_branch"],
                "parent": parent_info.get("full_name", "unknown"),
                "parent_branch": parent_info.get(
                    "default_branch", r["default_branch"]
                ),
            })
    else:
        forks = get_fork_repos(username)

    if not forks:
        print("No fork repos found.")
        return EXIT_OK

    print(f"Found {len(forks)} fork repo(s):\n")
    for f in forks:
        print(f"  {f['full_name']}")
        print(f"    branch: {f['branch']}, upstream: {f['parent']}")
    print()

    # ── Execute ──
    success = 0
    safety_abort = 0

    for f in forks:
        print(f"[{f['name']}]")
        if add_mode:
            ok = add_sync_workflow(
                f["full_name"], f["branch"], username, dry_run, force,
            )
        else:
            ok = sync_now(
                f["full_name"], f["branch"],
                f["parent"], f["parent_branch"],
                dry_run, force,
            )
        if ok:
            success += 1
        elif not add_mode:
            safety_abort += 1
        print()

    action = "updated" if add_mode else "synced"
    print(f"Done: {success}/{len(forks)} repos {action}.")

    return EXIT_SAFETY if safety_abort else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
