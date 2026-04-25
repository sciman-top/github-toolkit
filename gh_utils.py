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

NOTE: Supports environment tokens (GH_TOKEN/GITHUB_TOKEN) and gh CLI local
login. Prefer environment tokens for automation. Do NOT handle secrets/tokens
in code. Upgrade to encrypted credential storage (e.g. keyring / SSH key)
remains a future option if the repository adopts a stricter auth posture.
"""

import json
import os
import re
import shutil
# gh CLI is invoked with argv lists only.
import subprocess  # nosec B404
import sys
import time
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────
DELETION_THRESHOLD = 0.5   # Abort sync if upstream < fork * this
SYNC_CRON = "17 2 * * 0"  # Every Sunday 02:17 UTC
FORK_DISCOVERY_ENDPOINT = "user/repos?affiliation=owner&visibility=all"
API_TIMEOUT = 30           # Seconds per API call
MAX_RETRIES = 2            # Retries on transient failures
RETRY_BASE_DELAY = 2       # Seconds (multiplied by attempt number)
MAX_PAGES = 50             # Safety limit for pagination (5000 items)
AUTH_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN")
WINDOWS_REQUIRED_ENV_VARS = (
    "SystemRoot",
    "WINDIR",
    "ComSpec",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "ProgramFiles",
)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
NETWORK_ERROR_MARKERS = (
    "connection reset",
    "connection refused",
    "connection timed out",
    "context deadline exceeded",
    "eof",
    "i/o timeout",
    "no such host",
    "temporary failure",
    "tls handshake timeout",
)
AUTH_ERROR_MARKERS = (
    "gh auth login",
    "not logged into any github hosts",
    "populate the gh_token",
    "authentication required",
)
HTTP_STATUS_PATTERNS = (
    re.compile(r"\bHTTP\s+(\d{3})\b", re.IGNORECASE),
    re.compile(r"\((?:HTTP\s+)?(\d{3})\)", re.IGNORECASE),
)
ERROR_CODE_BY_ERROR_TYPE = {
    "environment": "E_GH_ENVIRONMENT",
    "timeout": "E_GH_TIMEOUT",
    "rate_limit": "E_GH_RATE_LIMIT",
    "server": "E_GH_SERVER",
    "network": "E_GH_NETWORK",
    "auth": "E_GH_AUTH",
    "not_found": "E_GH_NOT_FOUND",
    "invalid_response": "E_GH_INVALID_RESPONSE",
    "retry_exhausted": "E_GH_RETRY_EXHAUSTED",
    "unknown": "E_GH_UNKNOWN",
}

# ── Windows process environment ───────────────────────────────

def _prepend_path_entry(entry):
    """Prepend a PATH entry if it exists and is not already present."""
    if not entry or not os.path.isdir(entry):
        return
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    if any(os.path.normcase(p) == os.path.normcase(entry) for p in parts):
        return
    os.environ["PATH"] = os.pathsep.join([entry] + parts)


def ensure_windows_process_environment():
    """Restore Windows base environment variables commonly stripped by hosts."""
    if os.name != "nt":
        return {}

    repaired = {}
    system_root = (
        os.environ.get("SystemRoot")
        or os.environ.get("WINDIR")
        or os.path.dirname(os.environ.get("ComSpec", ""))
        or r"C:\Windows"
    )
    if os.path.basename(system_root).lower() == "system32":
        system_root = os.path.dirname(system_root)

    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        home_drive = os.environ.get("HOMEDRIVE", "")
        home_path = os.environ.get("HOMEPATH", "")
        user_profile = f"{home_drive}{home_path}" if home_drive and home_path else None

    defaults = {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "ComSpec": os.path.join(system_root, "System32", "cmd.exe"),
        "PROGRAMDATA": os.environ.get("PROGRAMDATA") or r"C:\ProgramData",
        "ProgramFiles": os.environ.get("ProgramFiles") or r"C:\Program Files",
    }
    if user_profile:
        defaults["APPDATA"] = os.path.join(user_profile, "AppData", "Roaming")
        defaults["LOCALAPPDATA"] = os.path.join(user_profile, "AppData", "Local")

    for key in WINDOWS_REQUIRED_ENV_VARS:
        value = defaults.get(key)
        if value and not os.environ.get(key):
            os.environ[key] = value
            repaired[key] = value

    _prepend_path_entry(os.path.join(system_root, "System32"))
    _prepend_path_entry(system_root)
    _prepend_path_entry(os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
    ))
    _prepend_path_entry(os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "GitHub CLI",
    ))
    return repaired


ensure_windows_process_environment()

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


class AuthContextError(RuntimeError):
    """Structured authentication failure for text and JSON callers."""

    def __init__(self, auth_info, error_type, message, detail=None):
        super().__init__(message)
        self.auth_info = auth_info
        self.error_type = error_type
        self.message = message
        self.detail = detail

    def to_dict(self, tool):
        """Serialize auth failure for JSON CLI output."""
        payload = {
            "tool": tool,
            "error": "authentication_failed",
            "error_code": "E_AUTH_CONTEXT_FAILED",
            "error_type": self.error_type,
            "message": self.message,
            "auth": self.auth_info,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ForkDiscoveryError(RuntimeError):
    """Structured fork discovery failure for text and JSON callers."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self, tool):
        """Serialize fork discovery failure for JSON CLI output."""
        payload = {
            "tool": tool,
            "error": "fork_discovery_failed",
            "error_code": "E_FORK_DISCOVERY_FAILED",
            "error_type": "discovery",
            "message": self.message,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def detect_auth_mode(timeout=5):
    """Detect which supported authentication mode is currently active."""
    for env_var in AUTH_ENV_VARS:
        if os.environ.get(env_var):
            return {
                "configured": True,
                "mode": "env_token",
                "source": env_var,
            }

    if GH is None:
        return {
            "configured": False,
            "mode": "unavailable",
            "source": None,
        }

    try:
        result = subprocess.run(
            [GH, "auth", "status"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )  # nosec B603
    except subprocess.TimeoutExpired:
        return {
            "configured": False,
            "mode": "unknown",
            "source": "gh auth status timeout",
        }

    if result.returncode == 0:
        return {
            "configured": True,
            "mode": "gh_login",
            "source": "gh auth login",
        }

    return {
        "configured": False,
        "mode": "unconfigured",
        "source": None,
    }


def format_auth_hint():
    """Return a short authentication help message for CLI users."""
    return "Set GH_TOKEN/GITHUB_TOKEN or run: gh auth login"


def get_auth_context():
    """Return username + auth info, or raise AuthContextError on failure."""
    auth_info = detect_auth_mode()
    user = gh_api("user")
    username = user.get("login")
    if username:
        return {
            "username": username,
            "auth": auth_info,
        }

    detail = user.get("error") if isinstance(user, dict) else None
    if auth_info.get("configured") and isinstance(user, dict):
        error_type = user.get("error_type", "auth")
    else:
        error_type = "auth"

    if auth_info.get("configured"):
        message = (
            "Authentication is configured but GitHub user lookup failed. "
            "Verify the token or gh login still has API access."
        )
    else:
        message = f"Not authenticated. {format_auth_hint()}"

    raise AuthContextError(
        auth_info=auth_info,
        error_type=error_type,
        message=message,
        detail=detail,
    )


# ── API core ──────────────────────────────────────────────────

def _build_error_result(message, error_type="unknown", status_code=None,
                        retryable=False, detail=None, error_code=None):
    """Return a normalized gh_api error payload."""
    resolved_error_code = error_code or ERROR_CODE_BY_ERROR_TYPE.get(
        error_type,
        "E_GH_UNKNOWN",
    )
    payload = {
        "error": message,
        "error_code": resolved_error_code,
        "error_type": error_type,
        "status_code": status_code,
        "retryable": retryable,
    }
    if detail is not None:
        payload["detail"] = detail
    return payload


def _extract_status_code(text):
    """Best-effort HTTP status code extraction from gh stderr text."""
    if not text:
        return None
    for pattern in HTTP_STATUS_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _classify_gh_error(stderr):
    """Classify gh CLI stderr into a normalized error payload."""
    message = (stderr or "").strip() or "gh api failed"
    lowered = message.lower()
    status_code = _extract_status_code(message)

    if "rate limit" in lowered:
        return _build_error_result(
            message, error_type="rate_limit", status_code=status_code or 429,
            retryable=True,
        )

    if status_code in TRANSIENT_STATUS_CODES:
        return _build_error_result(
            message, error_type="server", status_code=status_code,
            retryable=True,
        )

    if any(marker in lowered for marker in NETWORK_ERROR_MARKERS):
        return _build_error_result(
            message, error_type="network", status_code=status_code,
            retryable=True,
        )

    if any(marker in lowered for marker in AUTH_ERROR_MARKERS):
        return _build_error_result(
            message, error_type="auth", status_code=status_code,
        )

    if status_code in {401, 403}:
        return _build_error_result(
            message, error_type="auth", status_code=status_code,
        )

    if status_code == 404:
        return _build_error_result(
            message, error_type="not_found", status_code=status_code,
        )

    return _build_error_result(
        message, error_type="unknown", status_code=status_code,
    )


def gh_api(endpoint, method="GET", payload=None, timeout=API_TIMEOUT):
    """Call GitHub API via gh CLI.

    Returns parsed JSON dict on success, or {"error": ...} on failure.
    Retries on timeout and retryable gh/API failures with linear backoff.
    """
    if GH is None:
        return _build_error_result("gh CLI not found", error_type="environment")

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
                shell=False,
                timeout=timeout,
            )  # nosec B603
        except subprocess.TimeoutExpired:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue
            return _build_error_result(
                f"API timeout ({timeout}s): {endpoint}",
                error_type="timeout",
                retryable=True,
            )

        if result.returncode != 0:
            error_result = _classify_gh_error(result.stderr)
            if error_result["retryable"] and attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * (attempt + 1))
                continue
            return error_result

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raw = (result.stdout or "").strip()
            detail = raw[:200] if raw else "<empty response>"
            return _build_error_result(
                f"Invalid JSON response for endpoint: {endpoint}",
                error_type="invalid_response",
                detail=detail,
            )

    # Should not reach here, but defensive fallback
    return _build_error_result(
        "Max retries exceeded", error_type="retry_exhausted", retryable=True,
    )


def paginate(endpoint, per_page=100, include_error=False):
    """Fetch all pages of a paginated GitHub API endpoint.

    Returns a flat list of items by default.
    If include_error=True, returns (items, error_payload_or_none).
    """
    sep = "&" if "?" in endpoint else "?"
    results = []
    page_error = None
    for page in range(1, MAX_PAGES + 1):
        data = gh_api(f"{endpoint}{sep}per_page={per_page}&page={page}")
        if isinstance(data, dict):
            if "error" in data:
                page_error = data
                break
            # Single-item response (unusual but defensive)
            results.append(data)
            break
        if not data:
            break
        if not isinstance(data, list):
            break
        results.extend(data)
        if len(data) < per_page:
            break
    if include_error:
        return results, page_error
    return results


# ── Domain helpers ────────────────────────────────────────────

def get_current_user():
    """Return authenticated username. Exit on auth failure."""
    try:
        return get_auth_context()["username"]
    except AuthContextError as exc:
        print(f"ERROR: {exc.message}")
        sys.exit(1)


def get_repo_owner(full_name):
    """Return the owner/login portion of a repo full_name, or None."""
    if not full_name or "/" not in full_name:
        return None
    return full_name.split("/", 1)[0]


def get_compare_head(repo_full_name, branch):
    """Return OWNER:branch compare ref for a fork network repo, or None."""
    owner = get_repo_owner(repo_full_name)
    if not owner or not branch:
        return None
    return f"{owner}:{branch}"


def build_fork_record(repo):
    """Normalize a fork repository payload into the toolkit repo record shape."""
    if not isinstance(repo, dict) or not repo.get("fork"):
        return None

    full_name = repo.get("full_name")
    name = repo.get("name")
    branch = repo.get("default_branch")
    if not full_name or not name or not branch:
        return None

    parent_info = repo.get("parent") or {}
    parent_full_name = parent_info.get("full_name", "unknown")

    return {
        "name": name,
        "full_name": full_name,
        "branch": branch,
        "parent": parent_full_name,
        "parent_branch": parent_info.get("default_branch", branch),
        "parent_owner": get_repo_owner(parent_full_name),
    }


def utc_now_iso():
    """Return a compact UTC timestamp for machine-readable evidence."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def get_fork_repos_with_evidence(username):
    """Return fork repos plus live-discovery evidence.

    ``username`` is retained for the existing helper contract; discovery uses
    the authenticated-user repository endpoint so private owned forks are
    visible when the token has access.
    """
    all_repos, page_error = paginate(
        FORK_DISCOVERY_ENDPOINT,
        include_error=True,
    )
    if page_error:
        raise ForkDiscoveryError(
            "Failed to list fork repositories from GitHub API.",
            detail=page_error,
        )

    forks = []
    for r in all_repos:
        if not isinstance(r, dict):
            continue
        if not r.get("fork"):
            continue
        full_name = r["full_name"]
        # List API omits parent info — fetch per-repo for full details
        parent_info = r.get("parent")
        if not parent_info or not parent_info.get("full_name"):
            detail = gh_api(f"repos/{full_name}")
            if isinstance(detail, dict) and "error" not in detail:
                parent_info = detail.get("parent") or {}
            else:
                parent_info = {}
        fork = build_fork_record({**r, "parent": parent_info})
        if fork:
            forks.append(fork)

    evidence = {
        "source": "live_github_api",
        "endpoint": FORK_DISCOVERY_ENDPOINT,
        "fetched_at": utc_now_iso(),
        "authenticated_user": username,
        "repo_count": len(all_repos),
        "fork_count": len(forks),
    }
    return forks, evidence


def get_fork_repos(username):
    """Return list of fork repo dicts with keys:
    name, full_name, branch, parent, parent_branch.
    """
    forks, _evidence = get_fork_repos_with_evidence(username)
    return forks


def get_file_count(repo_name, branch):
    """Return blob count in a repo branch, or None on error/truncation."""
    result = gh_api(f"repos/{repo_name}/git/trees/{branch}?recursive=1")
    if "error" in result:
        return None
    if result.get("truncated"):
        print(f"  WARN: Tree for {repo_name} is truncated, file count unreliable.")
        return None
    return sum(1 for item in result.get("tree", []) if item.get("type") == "blob")
