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
import hashlib
import io
import json
import os
import re
import subprocess  # nosec B404
import sys
import tempfile

from gh_utils import (
    AuthContextError,
    DELETION_THRESHOLD,
    ForkDiscoveryError,
    GH,
    GhCliUnavailableError,
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
    is_not_found_error,
    parse_nonnegative_int,
    short_error,
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
MODE_VERIFY_RELEASES = "verify-releases"
MODE_SYNC_RELEASES = "sync-releases"
MODE_OPTION_FLAGS = (
    ("add_mode", "--add-workflow"),
    ("verify_releases", "--verify-releases"),
    ("sync_releases", "--sync-releases"),
)
VERIFY_RELEASE_SAFETY_SUMMARY_KEYS = (
    "latest_stable_missing_or_without_assets",
    "old_stable_assets_present",
)
FORCE_SYNC_ACK_ENV = "SYNC_FORKS_FORCE_ACK"
FORCE_SYNC_ACK_VALUE = "I_UNDERSTAND"
RELEASE_COMMAND_TIMEOUT = 600
COMPARE_FILES_LIMIT = 300
INSTALLABLE_RELEASE_ASSET_RE = re.compile(
    r"\.(?:exe|msi|deb|rpm|appimage|dmg|pkg|zip|tar\.gz)$",
    re.IGNORECASE,
)
SIGNED_RELEASE_ASSET_RE = re.compile(
    r"\.(?:sig|minisig|sigstore)$",
    re.IGNORECASE,
)
CHECKSUM_MANIFEST_ALGORITHMS = {
    "sha256sums": "sha256",
    "sha256sums.txt": "sha256",
    "sha512sums": "sha512",
    "sha512sums.txt": "sha512",
}

HELP_TEXT = """\
Usage:
  python sync-forks.py [options] [repo-name ...]

Options:
  --add-workflow   Add or update sync-fork.yml instead of syncing now
  --verify-releases Verify stable release mirror retention policy
  --sync-releases  Reconcile stable release mirror retention
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
    timeout-minutes: 30
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
          if ! printf '%s' "$COMPARE_JSON" | jq -e '.files | type == "array"' >/dev/null; then
            echo "::error::SAFETY ABORT: compare response files payload is malformed."
            exit 1
          fi
          COMPARE_FILE_COUNT=$(printf '%s' "$COMPARE_JSON" | jq -r '.files | length')
          if [ "$COMPARE_FILE_COUNT" -ge 300 ]; then
            echo "::error::SAFETY ABORT: compare file list may be truncated at 300 entries."
            exit 1
          fi
          DELETED_FILES=$(printf '%s' "$COMPARE_JSON" | jq -r '.files[]? | select(.status == "removed") | .filename')
          DELETED_FILE_COUNT=$(printf '%s' "$COMPARE_JSON" | jq -r '[.files[]? | select(.status == "removed")] | length')
          DELETE_LIMIT=$(( 5 + (FORK_FILES / 10) ))
          echo "Deleted files: $DELETED_FILE_COUNT"
          echo "Deletion limit: $DELETE_LIMIT"
          if [ "$DELETED_FILE_COUNT" -gt "$DELETE_LIMIT" ]; then
            echo "::error::SAFETY ABORT: deletion volume is too large for a safe sync."
            exit 1
          fi
          if printf '%s\\n' "$DELETED_FILES" | grep -Eqi '^(\\.github/|README(?:\\.[^/]*)?$|LICENSE(?:\\.[^/]*)?$|sync-fork\\.yml$)'; then
            echo "::error::SAFETY ABORT: critical path deletion detected."
            exit 1
          fi

      - name: Create restore point
        if: steps.upstream.outputs.skip != 'true'
        run: |
          set -euo pipefail
          BACKUP_BRANCH="backup/pre-sync"
          git branch -f "$BACKUP_BRANCH" HEAD
          git push origin "$BACKUP_BRANCH" --force

      - name: Merge upstream changes
        if: steps.upstream.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
        run: |
          set -euo pipefail
          gh api repos/${{{{ github.repository }}}}/merge-upstream -X POST -f branch=${{{{ github.event.repository.default_branch }}}}

      - name: Release retention policy
        if: steps.upstream.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{{{ secrets.SYNC_PAT }}}}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import hashlib
          import json
          import os
          import pathlib
          import re
          import subprocess
          import tempfile

          repo = os.environ["GITHUB_REPOSITORY"]
          installable_re = re.compile(r"\\.(?:exe|msi|deb|rpm|appimage|dmg|pkg|zip|tar\\.gz)$", re.IGNORECASE)
          signed_re = re.compile(r"\\.(?:sig|minisig|sigstore)$", re.IGNORECASE)
          checksum_algorithms = {{
              "sha256sums": "sha256",
              "sha256sums.txt": "sha256",
              "sha512sums": "sha512",
              "sha512sums.txt": "sha512",
          }}

          def gh_json(args):
              cmd = ["gh", *args]
              result = subprocess.run(
                  cmd, capture_output=True, text=True, check=False, timeout=600
              )
              if result.returncode != 0:
                  raise RuntimeError(result.stderr.strip() or "gh command failed")
              return json.loads(result.stdout)

          def gh_run(args):
              cmd = ["gh", *args]
              result = subprocess.run(
                  cmd, capture_output=True, text=True, check=False, timeout=600
              )
              if result.returncode != 0:
                  raise RuntimeError(result.stderr.strip() or "gh command failed")

          def stable(releases):
              return [
                  release for release in releases
                  if isinstance(release, dict)
                  and not release.get("draft")
                  and not release.get("prerelease")
              ]

          def required_asset_names(release):
              assets = release.get("assets") or []
              names = []
              for asset in assets:
                  name = asset.get("name")
                  if not isinstance(name, str):
                      continue
                  if (
                      not name
                      or name in {{".", ".."}}
                      or name.startswith("-")
                      or "/" in name
                      or "\\\\" in name
                      or "\\0" in name
                  ):
                      raise RuntimeError(f"unsafe release asset name: {{name!r}}")
                  lowered = name.lower()
                  if (
                      installable_re.search(name)
                      or signed_re.search(name)
                      or lowered == "latest.json"
                      or lowered in checksum_algorithms
                  ):
                      names.append(name)
              return names

          repo_meta = gh_json(["api", f"repos/{{repo}}"])
          upstream = ((repo_meta.get("parent") or {{}}).get("full_name") or "").strip()
          if not upstream:
              print("::notice::Upstream repo metadata is missing; release retention skipped.")
              raise SystemExit(0)

          upstream_releases = gh_json(["api", f"repos/{{upstream}}/releases?per_page=100"])
          fork_releases = gh_json(["api", f"repos/{{repo}}/releases?per_page=100"])
          upstream_stable = stable(upstream_releases)
          if not upstream_stable:
              print("::notice::source-only release notes policy: upstream has no stable releases.")
              raise SystemExit(0)

          latest = upstream_stable[0]
          tag = latest.get("tag_name")
          if not tag:
              print("::notice::Latest stable release has no tag; release retention skipped.")
              raise SystemExit(0)

          required = required_asset_names(latest)
          if not required:
              print("::notice::source-only release notes policy: upstream latest stable release has no mirrored assets.")
              raise SystemExit(0)

          latest_fork = None
          for release in fork_releases:
              if isinstance(release, dict) and release.get("tag_name") == tag:
                  latest_fork = release
                  break

          if latest_fork is None:
              payload_path = None
              try:
                  with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                      json.dump({{
                          "tag_name": tag,
                          "target_commitish": os.environ["GITHUB_REF_NAME"],
                          "name": latest.get("name") or tag,
                          "body": latest.get("body") or "",
                          "draft": False,
                          "prerelease": False,
                      }}, handle, ensure_ascii=False)
                      payload_path = handle.name
                  gh_run(["api", f"repos/{{repo}}/releases", "-X", "POST", "--input", payload_path])
                  latest_fork = {{"tag_name": tag, "assets": []}}
              finally:
                  if payload_path and os.path.exists(payload_path):
                      os.unlink(payload_path)

          fork_asset_names = {{
              asset.get("name")
              for asset in (latest_fork.get("assets") or [])
              if isinstance(asset, dict) and isinstance(asset.get("name"), str)
          }}
          missing = [name for name in required if name not in fork_asset_names]
          required_by_lower = {{name.lower(): name for name in required}}
          metadata_names = {{"latest.json", *checksum_algorithms}}
          if any(name.lower() in metadata_names for name in missing):
              for lowered in ("latest.json", *checksum_algorithms):
                  asset_name = required_by_lower.get(lowered)
                  if asset_name and asset_name not in missing:
                      missing.append(asset_name)

          if missing:
              with tempfile.TemporaryDirectory() as tmpdir:
                  for asset_name in missing:
                      gh_run([
                          "release", "download", tag,
                          "-R", upstream,
                          "-D", tmpdir,
                          "-p", asset_name,
                          "--clobber",
                      ])

                  latest_name = required_by_lower.get("latest.json")
                  latest_json = pathlib.Path(tmpdir) / latest_name if latest_name else None
                  if latest_json is not None and latest_json.exists():
                      upstream_prefix = f"https://github.com/{{upstream}}/releases/download/{{tag}}/"
                      fork_prefix = f"https://github.com/{{repo}}/releases/download/{{tag}}/"
                      text = latest_json.read_text(encoding="utf-8")
                      latest_json.write_text(text.replace(upstream_prefix, fork_prefix), encoding="utf-8")

                  for lowered, algorithm in checksum_algorithms.items():
                      manifest_name = required_by_lower.get(lowered)
                      if not manifest_name or latest_json is None:
                          continue
                      manifest = pathlib.Path(tmpdir) / manifest_name
                      if not manifest.exists() or not latest_json.exists():
                          continue
                      latest_hash = hashlib.new(
                          algorithm, latest_json.read_bytes()
                      ).hexdigest()
                      updated = []
                      matched = False
                      for line in manifest.read_text(encoding="utf-8").splitlines():
                          parts = line.split()
                          filename = parts[-1] if parts else ""
                          normalized = (
                              filename.lstrip("*")
                              .replace("\\\\", "/")
                              .rsplit("/", 1)[-1]
                              .lower()
                          )
                          if normalized == "latest.json":
                              updated.append(f"{{latest_hash}}  {{filename}}")
                              matched = True
                          else:
                              updated.append(line)
                      if not matched:
                          raise RuntimeError(
                              f"{{manifest_name}} does not contain latest.json"
                          )
                      manifest.write_text(
                          "\\n".join(updated) + "\\n", encoding="utf-8"
                      )

                  upload_files = [str(pathlib.Path(tmpdir) / asset_name) for asset_name in missing]
                  gh_run([
                      "release", "upload", tag, *upload_files,
                      "-R", repo, "--clobber",
                  ])

          old_stable = [
              release for release in stable(fork_releases)
              if release.get("tag_name") != tag
          ]
          for release in old_stable:
              old_tag = release.get("tag_name")
              if not old_tag:
                  continue
              for asset in release.get("assets") or []:
                  name = asset.get("name")
                  if not isinstance(name, str):
                      continue
                  lowered = name.lower()
                  if (
                      installable_re.search(name)
                      or signed_re.search(name)
                      or lowered == "latest.json"
                      or lowered in checksum_algorithms
                  ):
                      gh_run(["release", "delete-asset", old_tag, name, "-R", repo, "--yes"])
          PY
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
    return WORKFLOW_TEMPLATE.format(cron=cron)


def _needs_workflow_update(existing_file):
    """Return (needs_update: bool, reason: str)."""
    if not isinstance(existing_file, dict):
        return True, "invalid workflow metadata payload"

    if "error" in existing_file:
        if not is_not_found_error(existing_file):
            return True, "cannot verify existing workflow"
        return True, "missing file"

    workflow_text = _decode_workflow_content(existing_file)
    if workflow_text is None:
        return True, "cannot decode existing workflow"

    normalized_current = "\n".join(workflow_text.splitlines())
    normalized_expected = "\n".join(_render_workflow_template().splitlines())
    if normalized_current != normalized_expected:
        return True, "workflow template drift"

    return False, "already matches current workflow template"


def _base_fork_row(fork):
    """Return common report fields for fork-level verification rows."""
    return {
        "name": fork["name"],
        "full_name": fork["full_name"],
        "upstream": fork["parent"],
    }


def _print_safety_abort(message_lines):
    """Print a standardized safety abort banner plus detail lines."""
    print("  *** SAFETY ABORT ***")
    for line in message_lines:
        print(f"  {line}")


def _safe_compare_payload(compare):
    """Normalize compare response payload or return an abort reason."""
    if not isinstance(compare, dict) or "error" in compare:
        return None, "Cannot inspect upstream diff safely."
    if "files" not in compare:
        return None, "Compare response is missing files."

    files = compare.get("files", [])
    if not isinstance(files, list):
        return None, "Compare response files payload is malformed."
    if len(files) >= COMPARE_FILES_LIMIT:
        return None, (
            "Compare response file list may be truncated at "
            f"{COMPARE_FILES_LIMIT} entries."
        )

    deleted_files = []
    for item in files:
        if not isinstance(item, dict):
            return None, "Compare response file entry is malformed."
        if item.get("status") != "removed":
            continue
        filename = item.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            return None, "Compare response removed filename is malformed."
        deleted_files.append(filename)

    return {
        "deleted_file_count": len(deleted_files),
        "deleted_files": deleted_files,
    }, None


def _is_critical_deleted_path(path):
    """Return whether deleting a path crosses the sync safety boundary."""
    lowered = path.replace("\\", "/").lower()
    is_root_file = "/" not in lowered
    return (
        lowered.startswith(".github/")
        or lowered == "sync-fork.yml"
        or (is_root_file and lowered == "readme")
        or (is_root_file and lowered.startswith("readme."))
        or (is_root_file and lowered == "license")
        or (is_root_file and lowered.startswith("license."))
    )


def _old_stable_required_asset_count(releases, latest_tag):
    """Return retained old-stable assets that policy requires pruning."""
    return sum(
        len(_required_release_asset_names(release))
        for release in _stable_releases(releases)
        if release.get("tag_name") != latest_tag
    )


def _write_release_file(path, content):
    """Write one release helper file using UTF-8."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _rewrite_latest_json(latest_json_path, upstream, full_name, tag):
    """Rewrite latest.json URLs from upstream to fork download URLs."""
    if not os.path.isfile(latest_json_path):
        return False
    with open(latest_json_path, "r", encoding="utf-8") as handle:
        latest_json_text = handle.read()
    upstream_prefix = f"https://github.com/{upstream}/releases/download/{tag}/"
    fork_prefix = f"https://github.com/{full_name}/releases/download/{tag}/"
    _write_release_file(
        latest_json_path,
        latest_json_text.replace(upstream_prefix, fork_prefix),
    )
    return True


def _refresh_checksum_manifest_for_latest_json(
    manifest_path, latest_json_path, algorithm
):
    """Refresh a latest.json checksum entry, failing closed if absent."""
    if not os.path.isfile(manifest_path) or not os.path.isfile(latest_json_path):
        return True, None

    with open(latest_json_path, "rb") as handle:
        latest_hash = hashlib.new(algorithm, handle.read()).hexdigest()

    updated_lines = []
    matched_latest_json = False
    with open(manifest_path, "r", encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            parts = line.split()
            filename = parts[-1] if parts else ""
            normalized_name = (
                filename.lstrip("*")
                .replace("\\", "/")
                .rsplit("/", 1)[-1]
                .lower()
            )
            if normalized_name == "latest.json":
                updated_lines.append(f"{latest_hash}  {filename}")
                matched_latest_json = True
            else:
                updated_lines.append(line)

    if not matched_latest_json:
        return False, "checksum manifest does not contain latest.json"

    _write_release_file(manifest_path, "\n".join(updated_lines) + "\n")
    return True, None


def _expand_release_metadata_bundle(release, missing_assets):
    """Keep latest.json and checksum manifests synchronized as one bundle."""
    expanded = list(missing_assets)
    metadata_names = {"latest.json", *CHECKSUM_MANIFEST_ALGORITHMS}
    if not any(name.lower() in metadata_names for name in expanded):
        return expanded

    for asset_name in _required_release_asset_names(release):
        if asset_name.lower() in metadata_names and asset_name not in expanded:
            expanded.append(asset_name)
    return expanded


def _is_safe_release_asset_name(asset_name):
    """Reject asset names that can escape a temp dir or alter CLI parsing."""
    return (
        isinstance(asset_name, str)
        and bool(asset_name)
        and asset_name not in {".", ".."}
        and not asset_name.startswith("-")
        and "/" not in asset_name
        and "\\" not in asset_name
        and "\0" not in asset_name
    )


def _download_missing_release_assets(upstream, tag, missing_assets, temp_dir):
    """Download all missing assets and verify every expected file exists."""
    downloaded_paths = []
    for asset_name in missing_assets:
        if not _is_safe_release_asset_name(asset_name):
            return None, f"Unsafe release asset name: {asset_name!r}"
        result = _run_gh_command([
            "release",
            "download",
            tag,
            "-R",
            upstream,
            "-D",
            temp_dir,
            "-p",
            asset_name,
            "--clobber",
        ])
        if result.returncode != 0:
            return None, result.stderr.strip()[:200] or "cannot download release asset"

        asset_path = os.path.join(temp_dir, asset_name)
        if not os.path.isfile(asset_path):
            return None, f"Downloaded release asset is missing: {asset_name}"
        downloaded_paths.append(asset_path)

    return downloaded_paths, None


def safety_check(fork_name, fork_branch, upstream_name, upstream_branch,
                 force=False):
    """Compare file counts and deletions. Return SAFE/ABORT/SKIP."""
    if force:
        print("  [FORCE] Safety check bypassed.")
        return SAFE

    fork_count = get_file_count(fork_name, fork_branch)
    upstream_count = get_file_count(upstream_name, upstream_branch)

    if fork_count is None:
        _print_safety_abort((
            "Cannot get fork file count, safety baseline is unavailable.",
            "Sync skipped to preserve fork.",
            "Use --force to override (NOT recommended).",
        ))
        return ABORT
    if upstream_count is None:
        print("  WARN: Upstream unavailable or deleted. Skipping sync to preserve fork.")
        return SKIP

    print(f"  Files: fork={fork_count}, upstream={upstream_count}")

    if upstream_count < fork_count * DELETION_THRESHOLD:
        _print_safety_abort((
            f"Upstream has {upstream_count} files, fork has {fork_count}.",
            "Upstream may have been wiped. Sync skipped for safety.",
            "Use --force to override (NOT recommended).",
        ))
        return ABORT

    compare_head = get_compare_head(upstream_name, upstream_branch)
    compare_endpoint = build_compare_endpoint(fork_name, fork_branch, compare_head)
    if not compare_endpoint:
        print("  ABORT: Upstream metadata is incomplete, cannot inspect diff safely.")
        return ABORT
    compare_summary, compare_error = _safe_compare_payload(gh_api(compare_endpoint))
    if compare_error:
        print(f"  ABORT: {compare_error}")
        return ABORT

    deleted_file_count = compare_summary["deleted_file_count"]
    deleted_files = compare_summary["deleted_files"]
    deletion_limit = 5 + (fork_count // 10)

    print(f"  Deleted files: {deleted_file_count} (limit: {deletion_limit})")
    if deleted_file_count > deletion_limit:
        _print_safety_abort(("Deletion volume is too large for a safe sync.",))
        return ABORT

    critical_deleted = [
        path for path in deleted_files
        if _is_critical_deleted_path(path)
    ]
    if critical_deleted:
        _print_safety_abort(("Critical path deletions detected:",))
        for path in critical_deleted:
            print(f"    - {path}")
        return ABORT

    return True


def _verify_upstream_convergence(
    fork_name, fork_branch, upstream_name, upstream_branch
):
    """Verify a completed merge left no upstream commits unapplied."""
    compare_head = get_compare_head(upstream_name, upstream_branch)
    compare_endpoint = build_compare_endpoint(fork_name, fork_branch, compare_head)
    if not compare_endpoint:
        print("  SAFETY: Cannot build post-sync compare endpoint.")
        return False

    compare = gh_api(compare_endpoint)
    if not isinstance(compare, dict) or "error" in compare:
        print(f"  SAFETY: Cannot verify post-sync state: {short_error(compare)}")
        return False

    behind_by = parse_nonnegative_int(compare.get("ahead_by"))
    if behind_by is None:
        print("  SAFETY: Post-sync compare response has malformed ahead_by.")
        return False
    if behind_by:
        print(f"  SAFETY: Fork remains {behind_by} upstream commit(s) behind.")
        return False

    print("  Verified: fork is not behind upstream.")
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
    if "error" in existing and not is_not_found_error(existing):
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
        print(f"  FAIL: {short_error(result)}")
        return RESULT_ERROR

    url = result.get("content", {}).get("html_url", "ok")
    action = "Updated" if has_existing else "Added"
    print(f"  {action}: {url} (reason: {reason})")
    return RESULT_OK


def _create_restore_point(fork_name, fork_branch):
    """Create or update backup/pre-sync to the current fork branch head."""
    backup_ref = "backup/pre-sync"
    head_ref = gh_api(f"repos/{fork_name}/git/ref/heads/{fork_branch}")
    if not isinstance(head_ref, dict) or "error" in head_ref:
        print("  ABORT: Cannot read current fork head for restore point.")
        print(f"  Detail: {short_error(head_ref)}")
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
        print(f"  Detail: {short_error(update_result)}")
        return False

    if not is_not_found_error(update_result):
        print("  ABORT: Cannot update restore point branch.")
        print(f"  Detail: {short_error(update_result)}")
        return False

    create_result = gh_api(
        f"repos/{fork_name}/git/refs",
        method="POST",
        payload={"ref": f"refs/heads/{backup_ref}", "sha": head_sha},
    )
    if not isinstance(create_result, dict) or "error" in create_result:
        print("  ABORT: Cannot create restore point branch.")
        print(f"  Detail: {short_error(create_result)}")
        return False

    print(f"  Restore point created: {backup_ref} -> {head_sha[:12]}")
    return True


def _sync_now_result(fork_name, fork_branch, upstream_name, upstream_branch,
                     dry_run=False, force=False):
    """Perform a one-time sync with safety check. Return operation result."""
    safety_result = safety_check(
        fork_name, fork_branch, upstream_name, upstream_branch, force
    )
    if safety_result is SKIP:
        return RESULT_SAFETY
    if safety_result is ABORT:
        return RESULT_SAFETY

    if dry_run:
        print(
            f"  [DRY-RUN] Safety checks passed; would create/refresh "
            f"backup/pre-sync and sync {upstream_name} -> {fork_name}"
        )
        return RESULT_OK

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
        else:
            print(f"  FAIL: {result['error'][:200]}")
            return RESULT_ERROR
    else:
        print(f"  Synced: {result.get('message', 'ok')}")

    if not _verify_upstream_convergence(
        fork_name, fork_branch, upstream_name, upstream_branch
    ):
        return RESULT_SAFETY
    return RESULT_OK


def _resolve_specific_forks(username, repo_names):
    """Resolve explicitly requested repos. Return (forks, had_errors)."""
    forks = []
    had_errors = False
    for name in repo_names:
        full = name if "/" in name else f"{username}/{name}"
        repo = gh_api(f"repos/{full}")
        if isinstance(repo, dict) and "error" in repo:
            print(f"Error: {name} - {short_error(repo, limit=100)}")
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
        "verify_releases": False,
        "sync_releases": False,
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
            if arg == "--verify-releases":
                parsed["verify_releases"] = True
                continue
            if arg == "--sync-releases":
                parsed["sync_releases"] = True
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


def _required_release_asset_names(release):
    """Return release asset names that must be mirrored to the fork."""
    required = []
    for name in _asset_names(release):
        lowered = name.lower()
        if (
            INSTALLABLE_RELEASE_ASSET_RE.search(name)
            or SIGNED_RELEASE_ASSET_RE.search(name)
            or lowered == "latest.json"
            or lowered in CHECKSUM_MANIFEST_ALGORITHMS
        ):
            required.append(name)
    return required


def _missing_release_asset_names(upstream_release, fork_release):
    """Return required upstream asset names missing from the fork release."""
    upstream_required = _required_release_asset_names(upstream_release)
    fork_names = set(_asset_names(fork_release))
    return [name for name in upstream_required if name not in fork_names]


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
        "missing_latest_release_assets": [],
        "result": RESULT_OK,
    }

    upstream_releases = gh_api(f"repos/{upstream}/releases?per_page=100")
    fork_releases = gh_api(f"repos/{full_name}/releases?per_page=100")
    if not isinstance(upstream_releases, list):
        row["result"] = RESULT_ERROR
        row["release_error"] = short_error(upstream_releases)
        row["next_action"] = "investigate_release_api_errors"
        return row
    if not isinstance(fork_releases, list):
        row["result"] = RESULT_ERROR
        row["release_error"] = short_error(fork_releases)
        row["next_action"] = "investigate_release_api_errors"
        return row

    upstream_stable = _stable_releases(upstream_releases)
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
    row["missing_latest_release_assets"] = _missing_release_asset_names(
        latest_upstream,
        latest_fork,
    )
    row["old_stable_release_asset_count"] = _old_stable_required_asset_count(
        fork_releases,
        latest_tag,
    )

    row["next_action"] = _release_retention_next_action(
        row["latest_upstream_installable_asset_count"],
        latest_fork,
        row["fork_latest_stable_asset_count"]
        if not row["missing_latest_release_assets"] else 0,
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


def _run_gh_command(args, timeout=RELEASE_COMMAND_TIMEOUT):
    """Run gh with a bounded wait and normalize local execution failures."""
    cmd = [GH, *args] if GH else ["gh", *args]
    if GH is None:
        return subprocess.CompletedProcess(
            cmd,
            returncode=127,
            stdout="",
            stderr="gh CLI not found",
        )
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )  # nosec B603
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd,
            returncode=124,
            stdout="",
            stderr=f"gh command timed out after {timeout}s",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            cmd,
            returncode=127,
            stdout="",
            stderr=f"cannot execute gh CLI: {exc}",
        )


def _sync_latest_release_assets(fork, row):
    """Create/update the latest stable release and upload missing assets."""
    upstream = fork["parent"]
    full_name = fork["full_name"]
    tag = row.get("latest_upstream_stable")
    if not tag:
        print("  FAIL: Latest upstream stable tag is missing.")
        return False

    upstream_releases = gh_api(f"repos/{upstream}/releases?per_page=100")
    fork_releases = gh_api(f"repos/{full_name}/releases?per_page=100")
    if not isinstance(upstream_releases, list) or not isinstance(fork_releases, list):
        print("  FAIL: Cannot refresh release metadata for sync.")
        return False

    latest_upstream = _find_release_by_tag(upstream_releases, tag)
    latest_fork = _find_release_by_tag(fork_releases, tag)
    if latest_upstream is None:
        print("  FAIL: Latest upstream stable release is missing.")
        return False

    if latest_fork is None:
        payload_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
            ) as handle:
                json.dump({
                    "tag_name": tag,
                    "target_commitish": fork["branch"],
                    "name": latest_upstream.get("name") or tag,
                    "body": latest_upstream.get("body") or "",
                    "draft": False,
                    "prerelease": False,
                }, handle, ensure_ascii=False)
                payload_path = handle.name
            result = _run_gh_command([
                "api",
                f"repos/{full_name}/releases",
                "-X",
                "POST",
                "--input",
                payload_path,
            ])
            if result.returncode != 0:
                print(f"  FAIL: {result.stderr.strip()[:200] or 'cannot create release'}")
                return False
        finally:
            if payload_path and os.path.exists(payload_path):
                os.unlink(payload_path)
        latest_fork = {"tag_name": tag, "assets": []}

    missing_assets = _missing_release_asset_names(latest_upstream, latest_fork)
    if not missing_assets:
        print(f"  Latest stable release already has required assets: {tag}")
        return True

    missing_assets = _expand_release_metadata_bundle(
        latest_upstream,
        missing_assets,
    )
    required_by_lower = {
        name.lower(): name
        for name in _required_release_asset_names(latest_upstream)
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        download_paths, download_error = _download_missing_release_assets(
            upstream, tag, missing_assets, temp_dir,
        )
        if download_error:
            print(f"  FAIL: {download_error}")
            return False

        latest_name = required_by_lower.get("latest.json")
        latest_json_path = (
            os.path.join(temp_dir, latest_name)
            if latest_name else os.path.join(temp_dir, "latest.json")
        )
        _rewrite_latest_json(latest_json_path, upstream, full_name, tag)

        for manifest_name, algorithm in CHECKSUM_MANIFEST_ALGORITHMS.items():
            actual_name = required_by_lower.get(manifest_name)
            if not actual_name:
                continue
            manifest_path = os.path.join(temp_dir, actual_name)
            refreshed, refresh_error = (
                _refresh_checksum_manifest_for_latest_json(
                    manifest_path,
                    latest_json_path,
                    algorithm,
                )
            )
            if not refreshed:
                print(f"  FAIL: {actual_name}: {refresh_error}")
                return False

        upload_paths = download_paths
        result = _run_gh_command([
            "release",
            "upload",
            tag,
            *upload_paths,
            "-R",
            full_name,
            "--clobber",
        ])
        if result.returncode != 0:
            print(f"  FAIL: {result.stderr.strip()[:200] or 'cannot upload release assets'}")
            return False

    print(f"  Synced latest stable release assets: {tag}")
    return True


def _prune_old_stable_release_assets(fork, row):
    """Delete installable/support assets from old stable releases."""
    if row.get("old_stable_release_asset_count", 0) <= 0:
        return True

    full_name = fork["full_name"]
    latest_tag = row.get("latest_upstream_stable")
    fork_releases = gh_api(f"repos/{full_name}/releases?per_page=100")
    if not isinstance(fork_releases, list):
        print("  FAIL: Cannot refresh fork releases for pruning.")
        return False

    for release in _stable_releases(fork_releases):
        old_tag = release.get("tag_name")
        if old_tag == latest_tag or not old_tag:
            continue
        for asset_name in _required_release_asset_names(release):
            if not _is_safe_release_asset_name(asset_name):
                print(f"  FAIL: Unsafe release asset name: {asset_name!r}")
                return False
            result = _run_gh_command([
                "release",
                "delete-asset",
                old_tag,
                asset_name,
                "-R",
                full_name,
                "--yes",
            ])
            if result.returncode != 0:
                print(f"  FAIL: {result.stderr.strip()[:200] or 'cannot delete old release asset'}")
                return False

    print("  Pruned old stable release assets.")
    return True


def _sync_releases_result(fork, dry_run=False):
    """Reconcile stable release retention for one fork repository."""
    row = _build_verify_releases_row(fork)
    if row["result"] == RESULT_ERROR:
        print(f"  FAIL: {row.get('release_error') or 'release verification failed'}")
        return RESULT_ERROR

    if row["release_policy"] == "source_only_release_notes":
        print("  SKIP: source-only release notes policy requires no mirrored assets.")
        return RESULT_OK

    next_action = row.get("next_action")
    if next_action == "none":
        print("  Stable release retention already converged.")
        return RESULT_OK

    if dry_run:
        print(
            "  [DRY-RUN] Would reconcile release retention "
            f"for {fork['full_name']} ({next_action})"
        )
        return RESULT_OK

    if next_action in {"mirror_latest_stable_release", "mirror_latest_stable_assets"}:
        if not _sync_latest_release_assets(fork, row):
            return RESULT_ERROR
    if row.get("old_stable_release_asset_count", 0) > 0:
        if not _prune_old_stable_release_assets(fork, row):
            return RESULT_ERROR

    final_row = _build_verify_releases_row(fork)
    if final_row["result"] == RESULT_ERROR:
        print(f"  FAIL: {final_row.get('release_error') or 'post-sync verification failed'}")
        return RESULT_ERROR
    if final_row.get("next_action") != "none":
        print(
            "  SAFETY: release retention did not converge after sync. "
            f"Next action: {final_row.get('next_action')}"
        )
        return RESULT_SAFETY
    print("  Release retention converged.")
    return RESULT_OK


def _mode_name(add_mode, verify_releases=False, sync_releases=False):
    """Return the machine-readable execution mode name."""
    if verify_releases:
        return MODE_VERIFY_RELEASES
    if sync_releases:
        return MODE_SYNC_RELEASES
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
    if mode in {MODE_SYNC, MODE_ADD_WORKFLOW, MODE_SYNC_RELEASES}:
        return True
    if mode == MODE_VERIFY_RELEASES:
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


def _run_sync_releases_mode(report, forks, dry_run, resolve_errors, json_output):
    """Execute sync-releases mode and return its exit result."""
    _print_fork_overview(forks, json_output)

    for fork in forks:
        if not json_output:
            print(f"[{fork['name']}]")
        result, output = _run_with_captured_stdout(
            _sync_releases_result,
            fork,
            dry_run,
        )
        if not json_output and output:
            print(output, end="")
        _append_operation_report(report, fork, result, output, json_output)
        if not json_output:
            print()

    if dry_run:
        report["message"] = (
            "Dry-run complete: "
            f"{report['summary']['success']}/{len(forks)} repos checked."
        )
    else:
        report["message"] = (
            f"Done: {report['summary']['success']}/{len(forks)} repos release-synced."
        )
    if not json_output:
        print(report["message"])

    if report["summary"]["failed"] or resolve_errors:
        return EXIT_ERROR, report
    if report["summary"]["safety_abort"]:
        return EXIT_SAFETY, report
    return EXIT_OK, report


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

    if dry_run:
        report["message"] = (
            "Dry-run complete: "
            f"{report['summary']['success']}/{len(forks)} repos checked."
        )
    else:
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
    verify_releases = parsed.get("verify_releases", False)
    sync_releases = parsed.get("sync_releases", False)
    specific_repos = parsed.get("specific_repos", [])
    json_output = parsed.get("json_output", False)

    check_gh_cli()
    auth_context = get_auth_context()
    auth_info = auth_context["auth"]
    username = auth_context["username"]

    mode = _mode_name(add_mode, verify_releases, sync_releases)
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

    if verify_releases:
        return _run_verify_releases_mode(report, forks, json_output)
    if sync_releases:
        return _run_sync_releases_mode(
            report, forks, dry_run, resolve_errors, json_output,
        )

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
    except (GhCliUnavailableError, AuthContextError, ForkDiscoveryError) as exc:
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
