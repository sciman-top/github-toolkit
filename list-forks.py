#!/usr/bin/env python3
"""
list-forks.py — List all fork repos and their sync status.

Usage:
  python list-forks.py
  python list-forks.py --json
"""

import json
import sys

from gh_utils import (
    AuthContextError,
    ForkDiscoveryError,
    check_gh_cli,
    get_compare_head,
    get_auth_context,
    get_fork_repos,
    gh_api,
)

HELP_TEXT = """\
Usage:
  python list-forks.py
  python list-forks.py --json
  python list-forks.py --help
"""


def _parse_cli_args(argv):
    """Parse list-forks CLI args."""
    show_help = False
    json_output = False

    for arg in argv:
        if arg in {"-h", "--help"}:
            show_help = True
            continue
        if arg == "--json":
            json_output = True
            continue
        return {
            "show_help": False,
            "json_output": json_output,
            "error": f"Unknown argument: {arg}",
        }

    return {"show_help": show_help, "json_output": json_output, "error": None}


def _print_help():
    """Print the list-forks CLI help text."""
    print(HELP_TEXT.rstrip())


def _collect_fork_statuses(forks):
    """Collect fork status rows for text and JSON renderers."""
    rows = []
    for fork in forks:
        wf = gh_api(
            f"repos/{fork['full_name']}/contents/.github/workflows/sync-fork.yml"
            f"?ref={fork['branch']}"
        )
        has_workflow = "error" not in wf

        behind_by = None
        compare_head = get_compare_head(fork["parent"], fork["parent_branch"])
        if compare_head:
            compare = gh_api(
                f"repos/{fork['full_name']}/compare/{fork['branch']}...{compare_head}"
            )
            if isinstance(compare, dict) and "behind_by" in compare:
                behind_by = compare["behind_by"]

        rows.append({
            "name": fork["name"],
            "full_name": fork["full_name"],
            "branch": fork["branch"],
            "upstream": fork["parent"],
            "upstream_branch": fork["parent_branch"],
            "behind_by": behind_by,
            "workflow_installed": has_workflow,
            "workflow_status": "Installed" if has_workflow else "Missing",
        })

    return rows


def main():
    parsed = _parse_cli_args(sys.argv[1:])
    if parsed["show_help"]:
        _print_help()
        return 0
    if parsed["error"]:
        if parsed["json_output"]:
            print(json.dumps({
                "tool": "list-forks",
                "error": parsed["error"],
                "error_code": "E_CLI_UNKNOWN_ARGUMENT",
                "error_type": "cli",
            }, ensure_ascii=False, indent=2))
            return 1
        print(f"Error: {parsed['error']}")
        print()
        _print_help()
        return 1

    check_gh_cli()
    try:
        auth_context = get_auth_context()
        auth_info = auth_context["auth"]
        username = auth_context["username"]
        forks = get_fork_repos(username)
    except (AuthContextError, ForkDiscoveryError) as exc:
        if parsed["json_output"]:
            print(json.dumps(exc.to_dict("list-forks"), ensure_ascii=False, indent=2))
            return 1
        print(f"ERROR: {exc.message}")
        return 1

    rows = _collect_fork_statuses(forks)

    if parsed["json_output"]:
        payload = {
            "tool": "list-forks",
            "user": username,
            "auth": auth_info,
            "summary": {
                "total": len(rows),
            },
            "repos": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not forks:
        print("No fork repos found.")
        return 0

    print(f"Fork repos for {username} ({len(forks)} total):\n")
    print(f"{'Repository':<35} {'Branch':<10} {'Upstream':<35} {'Behind':<8} {'Workflow':<10}")
    print("-" * 103)

    for row in rows:
        behind = "-" if row["behind_by"] is None else str(row["behind_by"])
        print(
            f"{row['name']:<35} {row['branch']:<10} {row['upstream']:<35} "
            f"{behind:<8} {row['workflow_status']:<10}"
        )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
