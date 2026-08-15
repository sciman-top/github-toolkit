#!/usr/bin/env python3
"""Focused behavior tests for GitHub Toolkit's safety-critical paths."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess  # nosec B404 - tests construct process results only
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import gh_utils


ROOT = os.path.dirname(os.path.abspath(__file__))


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_forks = load_script("sync_forks", "sync-forks.py")
list_forks = load_script("list_forks", "list-forks.py")

FORK = {
    "name": "fork",
    "full_name": "me/fork",
    "branch": "main",
    "parent": "upstream/repo",
    "parent_branch": "main",
}


def encoded(text):
    return base64.b64encode(text.encode()).decode()


class GhApiTests(unittest.TestCase):
    def test_error_classification_preserves_retry_and_auth_boundaries(self):
        rate_limit = gh_utils._classify_gh_error(
            "gh: API rate limit exceeded (HTTP 429)"
        )
        not_found = gh_utils._classify_gh_error("gh: Not Found (HTTP 404)")
        auth = gh_utils._classify_gh_error("please run: gh auth login")

        self.assertEqual(rate_limit["error_code"], "E_GH_RATE_LIMIT")
        self.assertTrue(rate_limit["retryable"])
        self.assertEqual(not_found["error_code"], "E_GH_NOT_FOUND")
        self.assertFalse(not_found["retryable"])
        self.assertEqual(auth["error_code"], "E_GH_AUTH")

    @patch("gh_utils.time.sleep", return_value=None)
    @patch("gh_utils.subprocess.run")
    def test_gh_api_retries_transient_failure(self, run, _sleep):
        run.side_effect = [
            Mock(returncode=1, stderr="Bad Gateway (HTTP 502)", stdout=""),
            Mock(returncode=0, stderr="", stdout='{"ok": true}'),
        ]

        self.assertEqual(gh_utils.gh_api("repos/me/fork"), {"ok": True})
        self.assertEqual(run.call_count, 2)

    @patch("gh_utils.time.sleep", return_value=None)
    @patch("gh_utils.subprocess.run")
    def test_gh_api_timeout_is_structured(self, run, _sleep):
        run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=1)

        result = gh_utils.gh_api("repos/me/fork", timeout=1)

        self.assertEqual(result["error_code"], "E_GH_TIMEOUT")
        self.assertTrue(result["retryable"])

    @patch("gh_utils.subprocess.run")
    def test_gh_api_rejects_invalid_json(self, run):
        run.return_value = Mock(returncode=0, stderr="", stdout="<html>")

        result = gh_utils.gh_api("repos/me/fork")

        self.assertEqual(result["error_code"], "E_GH_INVALID_RESPONSE")
        self.assertIn("<html>", result["detail"])

    @patch("gh_utils.subprocess.run")
    def test_gh_api_sends_empty_post_payload(self, run):
        run.return_value = Mock(returncode=0, stderr="", stdout='{"ok": true}')

        result = gh_utils.gh_api("repos/me/fork/dispatches", "POST", {})

        self.assertEqual(result, {"ok": True})
        self.assertIn("--input", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["input"], "{}")

    @patch("gh_utils.gh_api")
    def test_paginate_stops_on_api_error(self, api):
        api.side_effect = [
            [{"id": 1}] * 100,
            {"error": "rate limited", "error_code": "E_GH_RATE_LIMIT"},
        ]

        rows, error = gh_utils.paginate("user/repos", include_error=True)

        self.assertEqual(len(rows), 100)
        self.assertEqual(error["error_code"], "E_GH_RATE_LIMIT")


class AuthAndDiscoveryTests(unittest.TestCase):
    @patch.dict(os.environ, {"GH_TOKEN": "token"}, clear=True)  # nosec B105
    def test_environment_token_has_priority(self):
        self.assertEqual(gh_utils.detect_auth_mode()["source"], "GH_TOKEN")

    @patch.object(gh_utils, "GH", "gh")
    @patch.dict(os.environ, {}, clear=True)
    @patch("gh_utils.subprocess.run")
    def test_local_gh_login_is_supported(self, run):
        run.return_value = Mock(returncode=0, stdout="", stderr="")

        result = gh_utils.detect_auth_mode()

        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "gh_login")

    @patch("gh_utils.utc_now_iso", return_value="2026-08-15T00:00:00Z")
    @patch("gh_utils.gh_api")
    @patch("gh_utils.paginate")
    def test_discovery_hydrates_parent_and_returns_live_evidence(
        self, paginate, api, _now
    ):
        paginate.return_value = ([{
            "fork": True,
            "name": "fork",
            "full_name": "me/fork",
            "default_branch": "main",
        }], None)
        api.return_value = {
            "parent": {"full_name": "upstream/repo", "default_branch": "main"}
        }

        forks, evidence = gh_utils.get_fork_repos_with_evidence("me")

        self.assertEqual(forks, [FORK | {"parent_owner": "upstream"}])
        self.assertEqual(evidence["source"], "live_github_api")
        self.assertEqual(evidence["parent_detail_lookup_count"], 1)

    @patch("gh_utils.paginate")
    def test_discovery_fails_closed_on_partial_page_error(self, paginate):
        paginate.return_value = ([], {"error": "page failed"})

        with self.assertRaises(gh_utils.ForkDiscoveryError):
            gh_utils.get_fork_repos_with_evidence("me")

    @patch("gh_utils.gh_api", return_value={"parent": {}})
    @patch("gh_utils.paginate")
    def test_discovery_fails_closed_when_parent_is_missing(self, paginate, _api):
        paginate.return_value = ([{
            "fork": True,
            "name": "fork",
            "full_name": "me/fork",
            "default_branch": "main",
        }], None)

        with self.assertRaises(gh_utils.ForkDiscoveryError):
            gh_utils.get_fork_repos_with_evidence("me")


class WorkflowTests(unittest.TestCase):
    def test_rendered_workflow_keeps_only_active_safety_chain(self):
        workflow = sync_forks._render_workflow_template()

        self.assertIn('cron: "0 13 * * 5"', workflow)
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("backup/pre-sync", workflow)
        self.assertIn("critical path deletion detected", workflow)
        self.assertIn("merge-upstream", workflow)
        self.assertNotIn("Self-use source policy", workflow)
        self.assertNotIn("MIRROR_ONLY_SOURCE", workflow)

    def test_current_workflow_is_not_rewritten(self):
        workflow = sync_forks._render_workflow_template()

        needs_update, reason = sync_forks._needs_workflow_update({
            "content": encoded(workflow),
            "sha": "abc",
        })

        self.assertFalse(needs_update)
        self.assertIn("already matches", reason)

    @patch.object(sync_forks, "gh_api")
    def test_add_workflow_dry_run_never_puts(self, api):
        api.return_value = {
            "error": "Not Found (HTTP 404)",
            "error_type": "not_found",
            "status_code": 404,
        }

        result = sync_forks._add_sync_workflow_result(
            "me/fork", "main", dry_run=True
        )

        self.assertEqual(result, sync_forks.RESULT_OK)
        self.assertEqual(api.call_count, 1)

    @patch.object(sync_forks, "gh_api")
    def test_add_workflow_fails_closed_when_lookup_is_unauthorized(self, api):
        api.return_value = {"error": "Forbidden (HTTP 403)"}

        result = sync_forks._add_sync_workflow_result("me/fork", "main")

        self.assertEqual(result, sync_forks.RESULT_ERROR)
        self.assertEqual(api.call_count, 1)

    def test_cli_modes_are_mutually_exclusive(self):
        parsed = sync_forks._parse_cli_args([
            "--add-workflow", "--verify-releases"
        ])

        self.assertIn("Choose only one mode", parsed["error"])


class SafetyAndSyncTests(unittest.TestCase):
    @patch.object(sync_forks, "get_file_count")
    def test_force_bypasses_safety_reads(self, file_count):
        self.assertIs(sync_forks.safety_check("f", "main", "u/r", "main", True), True)
        file_count.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_file_ratio_abort_happens_before_compare(self, file_count, api):
        file_count.side_effect = [100, 20]

        result = sync_forks.safety_check("f", "main", "u/r", "main")

        self.assertIs(result, sync_forks.ABORT)
        api.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_critical_deletion_aborts(self, file_count, api):
        file_count.side_effect = [100, 100]
        api.return_value = {
            "total_deletions": 1,
            "files": [{"status": "removed", "filename": ".github/workflows/ci.yml"}],
        }

        self.assertIs(
            sync_forks.safety_check("f", "main", "u/r", "main"),
            sync_forks.ABORT,
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_safe_change_passes(self, file_count, api):
        file_count.side_effect = [100, 105]
        api.return_value = {
            "total_deletions": 2,
            "files": [{"status": "removed", "filename": "old.txt"}],
        }

        self.assertIs(
            sync_forks.safety_check("f", "main", "u/r", "main"),
            sync_forks.SAFE,
        )

    @patch.object(sync_forks, "gh_api")
    def test_restore_point_updates_current_head(self, api):
        api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            {"ref": "refs/heads/backup/pre-sync"},
        ]

        self.assertTrue(sync_forks._create_restore_point("me/fork", "main"))
        self.assertEqual(api.call_args_list[1].kwargs["method"], "PATCH")
        self.assertEqual(
            api.call_args_list[1].kwargs["payload"],
            {"sha": "0123456789abcdef", "force": True},
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "_create_restore_point", return_value=False)
    @patch.object(sync_forks, "safety_check", return_value=True)
    def test_restore_point_failure_blocks_merge(self, _safety, _restore, api):
        result = sync_forks._sync_now_result(
            "me/fork", "main", "upstream/repo", "main"
        )

        self.assertEqual(result, sync_forks.RESULT_SAFETY)
        api.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "safety_check")
    def test_dry_run_skips_safety_and_remote_calls(self, safety, api):
        result = sync_forks._sync_now_result(
            "me/fork", "main", "upstream/repo", "main", dry_run=True
        )

        self.assertEqual(result, sync_forks.RESULT_OK)
        safety.assert_not_called()
        api.assert_not_called()


class ReleaseTests(unittest.TestCase):
    def test_required_assets_ignore_uninstallable_extras(self):
        release = {"assets": [
            {"name": "app.zip"},
            {"name": "app.zip.sig"},
            {"name": "latest.json"},
            {"name": "notes.txt"},
        ]}

        self.assertEqual(
            sync_forks._required_release_asset_names(release),
            ["app.zip", "app.zip.sig", "latest.json"],
        )

    @patch.object(sync_forks, "gh_api")
    def test_release_verification_detects_missing_latest_assets(self, api):
        api.side_effect = [
            [{
                "tag_name": "v1",
                "draft": False,
                "prerelease": False,
                "assets": [{"name": "app.zip"}],
            }],
            [],
        ]

        row = sync_forks._build_verify_releases_row(FORK)

        self.assertEqual(row["release_policy"], "latest_stable_assets_only")
        self.assertEqual(row["next_action"], "mirror_latest_stable_release")

    @patch.object(sync_forks, "gh_api")
    def test_release_verification_skips_source_only_releases(self, api):
        api.side_effect = [
            [{
                "tag_name": "v1",
                "draft": False,
                "prerelease": False,
                "assets": [],
            }],
            [],
        ]

        row = sync_forks._build_verify_releases_row(FORK)

        self.assertEqual(row["release_policy"], "source_only_release_notes")
        self.assertEqual(row["next_action"], "none")

    @patch.object(sync_forks, "_build_verify_releases_row")
    @patch.object(sync_forks, "_sync_latest_release_assets")
    def test_release_dry_run_has_no_write(self, sync_assets, build_row):
        build_row.return_value = {
            "result": sync_forks.RESULT_OK,
            "release_policy": "latest_stable_assets_only",
            "next_action": "mirror_latest_stable_assets",
            "old_stable_release_asset_count": 0,
        }

        result = sync_forks._sync_releases_result(FORK, dry_run=True)

        self.assertEqual(result, sync_forks.RESULT_OK)
        sync_assets.assert_not_called()

    def test_latest_json_checksum_is_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            latest = os.path.join(directory, "latest.json")
            sums = os.path.join(directory, "SHA256SUMS.txt")
            with open(latest, "w", encoding="utf-8") as handle:
                handle.write('{"url": "fork"}')
            with open(sums, "w", encoding="utf-8") as handle:
                handle.write("old  latest.json\n")

            ok, error = sync_forks._refresh_sha256_sums_for_latest_json(
                sums, latest
            )

            with open(latest, "rb") as handle:
                expected = hashlib.sha256(handle.read()).hexdigest()
            with open(sums, "r", encoding="utf-8") as handle:
                content = handle.read()
            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertIn(expected, content)


class CliAndReportingTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_real_force_write_requires_acknowledgement(self):
        self.assertTrue(
            sync_forks._force_confirmation_missing(
                True, sync_forks.MODE_SYNC, False
            )
        )
        self.assertFalse(
            sync_forks._force_confirmation_missing(
                True, sync_forks.MODE_SYNC, True
            )
        )

    @patch.object(list_forks, "gh_api")
    def test_list_status_uses_upstream_ahead_count(self, api):
        api.side_effect = [
            {"sha": "workflow"},
            {"ahead_by": 7},
        ]

        rows = list_forks._collect_fork_statuses([FORK])

        self.assertEqual(rows[0]["behind_by"], 7)
        self.assertTrue(rows[0]["workflow_installed"])

    @patch.object(list_forks, "_collect_fork_statuses", return_value=[])
    @patch.object(list_forks, "get_fork_repos_with_evidence")
    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_list_json_reports_live_discovery(
        self, _check, auth, discover, _collect
    ):
        auth.return_value = {
            "username": "me",
            "auth": {"mode": "gh_login", "source": "gh auth login"},
        }
        discover.return_value = ([], {"source": "live_github_api"})

        with patch.object(sys, "argv", ["list-forks.py", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = list_forks.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["discovery"]["source"], "live_github_api")

    @patch.object(sync_forks, "_run")
    def test_sync_json_emits_machine_report(self, run):
        run.return_value = (0, {
            "tool": "sync-forks",
            "mode": "sync",
            "summary": {"success": 1},
        })

        with patch.object(sys, "argv", ["sync-forks.py", "--json", "--dry-run"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["summary"]["success"], 1)

    def test_unknown_json_option_fails_before_runtime(self):
        with patch.object(sys, "argv", ["sync-forks.py", "--json", "--unknown"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                exit_code = sync_forks.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["error_code"], "E_CLI_UNKNOWN_OPTION")

    @patch.object(sync_forks, "_sync_now_result", return_value=sync_forks.RESULT_OK)
    @patch.object(sync_forks, "_resolve_specific_forks", return_value=([FORK], False))
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_run_routes_one_dry_run_through_the_sync_main_chain(
        self, _check, auth, _resolve, sync_now
    ):
        auth.return_value = {
            "username": "me",
            "auth": {"mode": "gh_login", "source": "gh auth login"},
        }
        parsed = sync_forks._parse_cli_args(["--json", "--dry-run", "fork"])

        exit_code, report = sync_forks._run(parsed)

        self.assertEqual(exit_code, sync_forks.EXIT_OK)
        self.assertEqual(report["mode"], sync_forks.MODE_SYNC)
        self.assertEqual(report["summary"]["success"], 1)
        sync_now.assert_called_once_with(
            "me/fork", "main", "upstream/repo", "main", True, False
        )


if __name__ == "__main__":
    unittest.main()
