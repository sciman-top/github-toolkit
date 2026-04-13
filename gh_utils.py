#!/usr/bin/env python3
"""
gh_utils.py — Shared utilities for GitHub Toolkit.

Provides:
  - Cross-platform gh CLI detection
  - gh_api(): GitHub API calls with timeout, retry, rate-limit awareness
  - paginate(): Generic paginated API queries
  - get_current_user(): Authenticated user lookup
  - get_fork_repos(): List all fork repos with parent info
  - get_file_count(): File count via git trees API
  - check_gh_cli(): Pre-flight CLI check

NOTE: Currently uses plaintext credential storage (gh CLI HTTPS token stored
in plain config). Do NOT handle secrets/tokens in code. Upgrade to encrypted
credential store (e.g. keyring / SSH key) planned for future release.
"""

import json
import os
import shutil
import subprocess
import sys
import time

# ── Config ────────────────────────────────────────────────────
DELETION_THRESHOLD = 0.5   # Abort sync if upstream < fork * this
SYNC_CRON = "0 2 * * 0"   # Every Sunday 02:00 UTC
API_TIMEOUT = 30           # Seconds per API call
MAX_RETRIES = 2            # Retries on transient failures
RETRY_BASE_DELAY = 2       # Seconds (multiplied by attempt number)
MAX_PAGES = 50             # Safety limit for pagination (5000 items)

# ── gh CLI path ───────────────────────────────────────────────

def _find_gh():
    """Locate gh CLI: PATH first, then Windows default location."""
    gh = shutil.which("gh")
    if gh:
        return gh
    win_path = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "GitHub CLI", "gh.exe",
    )
    if os.path.isfile(win_path):
        return win_path
    return None


GH = _find_gh()


def check_gh_cli():
    """Verify gh CLI is available. Exit with code 1 if not."""
    if GH is None:
        print("ERROR: GitHub CLI not found.")
        print("Run: setup.bat  or  winget install GitHub.cli")
        sys.exit(1)


# ── API core ──────────────────────────────────────────────────

def gh_api(endpoint, method="GET", payload=None, timeout=API_TIMEOUT):
    """Call GitHub API via gh CLI.

    Returns parsed JSON dict on success, or {"error": ...} on failure.
    Retries on timeout and rate-limit (403) with exponential backoff.
    """
    if GH is None:
        return {"error": "gh CLI not found"}

    cmd = [GH, "api", endpoint, "-X", method]
    if payload:
        cmd += ["--input", "-"]

    for attempt in range(MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd,
                input=json.dumps(payload) if payload else None,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue
            return {"error": f"API timeout ({timeout}s): {endpoint}"}

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Retry on rate limit
            if "403" in stderr and "rate limit" in stderr.lower():
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BASE_DELAY * (attempt + 1))
                    continue
            return {"error": stderr}

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"raw": result.stdout}

    # Should not reach here, but defensive fallback
    return {"error": "Max retries exceeded"}


def paginate(endpoint, per_page=100):
    """Fetch all pages of a paginated GitHub API endpoint.

    Returns a flat list of items.
    """
    sep = "&" if "?" in endpoint else "?"
    results = []
    for page in range(1, MAX_PAGES + 1):
        data = gh_api(f"{endpoint}{sep}per_page={per_page}&page={page}")
        if isinstance(data, dict):
            if "error" in data:
                break
            # Single-item response (unusual but defensive)
            results.append(data)
            break
        if not data:
            break
        results.extend(data)
        if len(data) < per_page:
            break
    return results


# ── Domain helpers ────────────────────────────────────────────

def get_current_user():
    """Return authenticated username. Exit on auth failure."""
    user = gh_api("user")
    username = user.get("login")
    if not username:
        print("ERROR: Not authenticated. Run: gh auth login")
        sys.exit(1)
    return username


def get_fork_repos(username):
    """Return list of fork repo dicts with keys:
    name, full_name, branch, parent, parent_branch.
    """
    all_repos = paginate(f"users/{username}/repos?type=forks")

    forks = []
    for r in all_repos:
        if not r.get("fork"):
            continue
        full_name = r["full_name"]
        # List API omits parent info — fetch per-repo for full details
        parent_info = r.get("parent")
        if not parent_info or not parent_info.get("full_name"):
            detail = gh_api(f"repos/{full_name}")
            parent_info = detail.get("parent") or {}
        forks.append({
            "name": r["name"],
            "full_name": full_name,
            "branch": r["default_branch"],
            "parent": parent_info.get("full_name", "unknown"),
            "parent_branch": parent_info.get(
                "default_branch", r["default_branch"]
            ),
        })
    return forks


def get_file_count(repo_name, branch):
    """Return blob count in a repo branch, or None on error."""
    result = gh_api(f"repos/{repo_name}/git/trees/{branch}?recursive=1")
    if "error" in result:
        return None
    return sum(1 for item in result.get("tree", []) if item.get("type") == "blob")
