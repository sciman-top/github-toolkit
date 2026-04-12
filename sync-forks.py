#!/usr/bin/env python3
"""
sync-forks.py — Sync fork repos from upstream with safety checks.

Safety mechanism:
  Before syncing, compares file counts between fork and upstream.
  If upstream has significantly fewer files (suggesting deletion/wipe),
  the sync is ABORTED. Threshold: upstream < 50% of fork's file count.

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
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout fork
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Safety check — compare file counts
        run: |
          git remote add upstream ${{{{ github.event.repository.parent.clone_url }}}}
          git fetch upstream ${{{{ github.event.repository.default_branch }}}}
          FORK_FILES=$(git ls-tree -r --name-only HEAD | wc -l)
          UPSTREAM_FILES=$(git ls-tree -r --name-only upstream/${{{{ github.event.repository.default_branch }}}} | wc -l)
          echo "Fork files: $FORK_FILES"
          echo "Upstream files: $UPSTREAM_FILES"
          if [ "$UPSTREAM_FILES" -lt "$(echo "$FORK_FILES * {threshold} / 1" | bc)" ]; then
            echo "::error::SAFETY ABORT: Upstream has $UPSTREAM_FILES files vs fork's $FORK_FILES. Possible upstream deletion. Sync skipped."
            exit 1
          fi
          echo "Safety check passed."

      - name: Sync upstream changes
        uses: tgymnich/fork-sync@v1.9
        with:
          owner: {owner}
          token: ${{{{ secrets.GITHUB_TOKEN }}}}
          base: ${{{{ github.event.repository.default_branch }}}}
          head: ${{{{ github.event.repository.default_branch }}}}
'''


# ── Core functions ────────────────────────────────────────────

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
        print("  ABORT: Cannot reach upstream. May have been deleted.")
        return False

    print(f"  Files: fork={fork_count}, upstream={upstream_count}")

    if upstream_count < fork_count * DELETION_THRESHOLD:
        print("  *** SAFETY ABORT ***")
        print(f"  Upstream has {upstream_count} files, fork has {fork_count}.")
        print("  Upstream may have been wiped. Sync skipped for safety.")
        print("  Use --force to override (NOT recommended).")
        return False

    return True


def add_sync_workflow(repo_name, branch, username, dry_run=False, force=False):
    """Add or update sync-fork.yml in a repo. Return True on success."""
    existing = gh_api(
        f"repos/{repo_name}/contents/.github/workflows/sync-fork.yml?ref={branch}"
    )
    has_existing = "error" not in existing

    if has_existing and not force:
        print("  SKIP: sync-fork.yml already exists (use --force to overwrite)")
        return True

    content = WORKFLOW_TEMPLATE.format(
        owner=username, cron=SYNC_CRON, threshold=DELETION_THRESHOLD,
    )
    encoded = base64.b64encode(content.encode()).decode()

    if dry_run:
        action = "update" if has_existing else "add"
        print(f"  [DRY-RUN] Would {action} sync-fork.yml (branch: {branch})")
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
    print(f"  {action}: {url}")
    return True


def sync_now(fork_name, fork_branch, upstream_name, upstream_branch,
             dry_run=False, force=False):
    """Perform a one-time sync with safety check. Return True on success."""
    if dry_run:
        print(f"  [DRY-RUN] Would sync {upstream_name} -> {fork_name}")
        return True

    if not safety_check(fork_name, fork_branch,
                        upstream_name, upstream_branch, force):
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
