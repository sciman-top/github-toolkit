#!/usr/bin/env python3
"""
list-forks.py — List all fork repos and their sync status.

Usage:
  python list-forks.py
"""

import sys

from gh_utils import (
    check_gh_cli,
    get_current_user,
    get_fork_repos,
    gh_api,
)


def main():
    check_gh_cli()
    username = get_current_user()
    forks = get_fork_repos(username)

    if not forks:
        print("No fork repos found.")
        return 0

    print(f"Fork repos for {username} ({len(forks)} total):\n")
    print(f"{'Repository':<35} {'Branch':<10} {'Upstream':<35} {'Behind':<8} {'Workflow':<10}")
    print("-" * 103)

    for f in forks:
        name = f["name"]
        branch = f["branch"]
        parent = f["parent"]

        # Check sync workflow existence
        wf = gh_api(
            f"repos/{f['full_name']}/contents/.github/workflows/sync-fork.yml"
            f"?ref={branch}"
        )
        has_wf = "error" not in wf

        # Check how many commits behind upstream
        behind = "-"
        if parent and parent != "unknown":
            compare = gh_api(
                f"repos/{f['full_name']}/compare/{branch}..."
                f"{parent.replace('/', ':')}:{f['parent_branch']}"
            )
            if isinstance(compare, dict) and "behind_by" in compare:
                behind = str(compare["behind_by"])

        status = "Installed" if has_wf else "Missing"
        print(f"{name:<35} {branch:<10} {parent:<35} {behind:<8} {status:<10}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
