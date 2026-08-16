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
    build_compare_endpoint,
    encode_url_component,
    ForkDiscoveryError,
    GhCliUnavailableError,
    check_gh_cli,
    get_compare_head,
    get_auth_context,
    get_fork_repos_with_evidence,
    gh_api,
    is_not_found_error,
    parse_nonnegative_int,
    short_error,
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
    json_output = "--json" in argv

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
        branch = fork.get("branch")
        parent = fork.get("parent")
        parent_branch = fork.get("parent_branch")
        full_name = fork.get("full_name")

        row = {
            "name": fork["name"],
            "full_name": fork["full_name"],
            "branch": fork["branch"],
            "upstream": fork["parent"],
            "upstream_branch": fork["parent_branch"],
            "behind_by": None,
            "workflow_installed": False,
            "workflow_status": "Unknown",
            "result": "ok",
        }

        encoded_branch = encode_url_component(branch)
        if full_name and encoded_branch:
            wf = gh_api(
                f"repos/{full_name}/contents/.github/workflows/sync-fork.yml"
                f"?ref={encoded_branch}"
            )
            if isinstance(wf, dict) and "error" not in wf:
                row["workflow_installed"] = True
                row["workflow_status"] = "Installed"
            elif is_not_found_error(wf):
                row["workflow_status"] = "Missing"
            else:
                row["result"] = "error"
                row["workflow_error"] = short_error(wf)
        else:
            row["result"] = "error"
            row["workflow_error"] = "invalid repository or branch metadata"

        compare_head = get_compare_head(parent, parent_branch)
        compare_endpoint = build_compare_endpoint(
            full_name,
            branch,
            compare_head,
        )
        if compare_endpoint:
            compare = gh_api(compare_endpoint)
            if isinstance(compare, dict) and "error" not in compare:
                behind_by = parse_nonnegative_int(compare.get("ahead_by"))
                if behind_by is not None:
                    row["behind_by"] = behind_by
                else:
                    row["result"] = "error"
                    row["compare_error"] = "compare response has malformed ahead_by"
            else:
                row["result"] = "error"
                row["compare_error"] = short_error(compare)
        else:
            row["result"] = "error"
            row["compare_error"] = "invalid fork or upstream branch metadata"

        rows.append(row)

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

    try:
        check_gh_cli()
        auth_context = get_auth_context()
        auth_info = auth_context["auth"]
        username = auth_context["username"]
        forks, discovery = get_fork_repos_with_evidence(username)
    except (GhCliUnavailableError, AuthContextError, ForkDiscoveryError) as exc:
        if parsed["json_output"]:
            print(json.dumps(exc.to_dict("list-forks"), ensure_ascii=False, indent=2))
            return 1
        print(f"ERROR: {exc.message}")
        return 1

    rows = _collect_fork_statuses(forks)
    error_count = sum(row["result"] == "error" for row in rows)

    if parsed["json_output"]:
        payload = {
            "tool": "list-forks",
            "user": username,
            "auth": auth_info,
            "discovery": discovery,
            "summary": {
                "total": len(rows),
                "errors": error_count,
            },
            "repos": rows,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if error_count else 0

    if not forks:
        print("No fork repos found.")
        print(
            "Discovery: live_github_api "
            f"{discovery['endpoint']} at {discovery['fetched_at']}"
        )
        return 0

    print(f"Fork repos for {username} ({len(forks)} total):\n")
    print(
        "Discovery: live_github_api "
        f"{discovery['endpoint']} at {discovery['fetched_at']} "
        f"(repos={discovery['repo_count']}, forks={discovery['fork_count']})"
    )
    print()
    print(f"{'Repository':<35} {'Branch':<10} {'Upstream':<35} {'Behind':<8} {'Workflow':<10}")
    print("-" * 103)

    for row in rows:
        behind = "-" if row["behind_by"] is None else str(row["behind_by"])
        print(
            f"{row['name']:<35} {row['branch']:<10} {row['upstream']:<35} "
            f"{behind:<8} {row['workflow_status']:<10}"
        )

    print()
    if error_count:
        print(f"ERROR: {error_count} repo status check(s) were incomplete.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
