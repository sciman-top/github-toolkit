#!/usr/bin/env python3
"""Unit tests for GitHub Toolkit core logic."""

import base64
import contextlib
import importlib.util
import io
import json
import os
# Tests construct TimeoutExpired only.
import subprocess  # nosec B404
import unittest
try:
    from unittest.mock import Mock, patch
except OSError:
    from test_support import Mock, patch  # type: ignore[assignment]

import gh_utils
from gh_utils import (
    build_compare_endpoint,
    build_fork_record,
    encode_compare_ref,
    encode_url_component,
    get_compare_head,
    get_file_count,
    get_fork_repos_with_evidence,
    paginate,
    parse_nonnegative_int,
)

# Load sync-forks module (hyphen in filename prevents normal import)
_here = os.path.dirname(os.path.abspath(__file__))


def _load_module(module_name, module_path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec: {module_name} -> {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_forks = _load_module("sync_forks", os.path.join(_here, "sync-forks.py"))
list_forks = _load_module("list_forks", os.path.join(_here, "list-forks.py"))
claude_hook = _load_module(
    "governed_pre_tool_use",
    os.path.join(_here, ".claude", "hooks", "governed-pre-tool-use.py"),
)


def _encode(text):
    """Base64-encode a string for GitHub contents API simulation."""
    return base64.b64encode(text.encode()).decode()


# ── _decode_workflow_content ──────────────────────────────────

class TestDecodeWorkflowContent(unittest.TestCase):
    def test_valid_content(self):
        text = "name: test\non: push\n"
        result = sync_forks._decode_workflow_content({"content": _encode(text)})
        self.assertEqual(result, text)

    def test_empty_content_value(self):
        result = sync_forks._decode_workflow_content({"content": ""})
        self.assertIsNone(result)

    def test_no_content_key(self):
        result = sync_forks._decode_workflow_content({})
        self.assertIsNone(result)

    def test_non_dict_input(self):
        self.assertIsNone(sync_forks._decode_workflow_content("string"))
        self.assertIsNone(sync_forks._decode_workflow_content(None))

    def test_invalid_base64(self):
        result = sync_forks._decode_workflow_content({"content": "!!bad!!"})
        self.assertIsNone(result)


# ── _has_workflow_dispatch ────────────────────────────────────

class TestHasWorkflowDispatch(unittest.TestCase):
    def test_with_dispatch(self):
        self.assertTrue(
            sync_forks._has_workflow_dispatch("on:\n  workflow_dispatch: {}\n")
        )

    def test_without_dispatch(self):
        self.assertFalse(
            sync_forks._has_workflow_dispatch("on:\n  push:\n    branches: [main]\n")
        )

    def test_commented_dispatch(self):
        self.assertFalse(
            sync_forks._has_workflow_dispatch(
                "on:\n  # workflow_dispatch: {}\n  push:\n"
            )
        )

    def test_empty_and_none(self):
        self.assertFalse(sync_forks._has_workflow_dispatch(""))
        self.assertFalse(sync_forks._has_workflow_dispatch(None))

    def test_dispatch_in_comment_only(self):
        self.assertFalse(
            sync_forks._has_workflow_dispatch(
                "on: push\n# workflow_dispatch: {}\n"
            )
        )

    def test_inline_dispatch(self):
        self.assertTrue(
            sync_forks._has_workflow_dispatch("on: { workflow_dispatch: {} }\n")
        )

    def test_array_dispatch(self):
        self.assertTrue(
            sync_forks._has_workflow_dispatch("on: [push, workflow_dispatch]\n")
        )

    def test_sequence_dispatch(self):
        self.assertTrue(
            sync_forks._has_workflow_dispatch("on:\n  - push\n  - workflow_dispatch\n")
        )

    def test_similar_key_is_not_dispatch(self):
        self.assertFalse(
            sync_forks._has_workflow_dispatch("on:\n  not_workflow_dispatch: {}\n")
        )

    def test_dispatch_text_outside_on_block_is_ignored(self):
        self.assertFalse(
            sync_forks._has_workflow_dispatch(
                "on:\n  push:\n\njobs:\n  test:\n    steps:\n"
                "      - run: echo workflow_dispatch:\n"
            )
        )


# ── _needs_workflow_update ────────────────────────────────────

class TestNeedsWorkflowUpdate(unittest.TestCase):
    def test_error_response_means_update(self):
        needs, reason = sync_forks._needs_workflow_update({"error": "404"})
        self.assertTrue(needs)
        self.assertEqual(reason, "missing file")

    def test_non_not_found_error_means_unverified(self):
        needs, reason = sync_forks._needs_workflow_update({
            "error": "gh: Forbidden (HTTP 403)",
            "error_type": "auth",
            "status_code": 403,
        })
        self.assertTrue(needs)
        self.assertEqual(reason, "cannot verify existing workflow")

    def test_no_dispatch_means_update(self):
        text = "name: test\non: push\n"
        needs, reason = sync_forks._needs_workflow_update({"content": _encode(text)})
        self.assertTrue(needs)
        self.assertIn("workflow_dispatch", reason)

    def test_current_template_means_no_update(self):
        text = sync_forks._render_workflow_template()
        needs, reason = sync_forks._needs_workflow_update({"content": _encode(text)})
        self.assertFalse(needs)
        self.assertIn("current workflow template", reason)

    def test_dispatch_only_stale_template_means_update(self):
        text = """\
name: Sync Fork
on:
  schedule:
    - cron: "0 2 * * 0"
  workflow_dispatch: {}

jobs:
  sync:
    steps:
      - name: Safety check - compare file counts
      - name: Merge upstream changes
"""
        needs, reason = sync_forks._needs_workflow_update({"content": _encode(text)})
        self.assertTrue(needs)
        self.assertIn("workflow template drift", reason)
        self.assertIn("actions/checkout@v6", reason)

    def test_cron_drift_means_update(self):
        text = sync_forks._render_workflow_template(cron="30 2 * * 0")
        needs, reason = sync_forks._needs_workflow_update({"content": _encode(text)})
        self.assertTrue(needs)
        self.assertIn("workflow template drift", reason)

    def test_stale_merge_upstream_branch_means_update(self):
        text = sync_forks._render_workflow_template().replace(
            "branch=${{ github.event.repository.default_branch }}",
            "branch=${{ steps.upstream.outputs.branch }}",
        )
        needs, reason = sync_forks._needs_workflow_update({"content": _encode(text)})
        self.assertTrue(needs)
        self.assertIn("workflow template drift", reason)

    def test_unreadable_content_means_update(self):
        needs, reason = sync_forks._needs_workflow_update({"content": "!!bad!!"})
        self.assertTrue(needs)
        self.assertIn("cannot decode", reason)

    def test_non_dict_payload_means_update(self):
        needs, reason = sync_forks._needs_workflow_update(["not-a-dict"])
        self.assertTrue(needs)
        self.assertIn("invalid workflow metadata payload", reason)


class TestAddSyncWorkflow(unittest.TestCase):
    @patch.object(sync_forks, "gh_api")
    def test_lookup_auth_error_fails_closed_even_in_dry_run(self, mock_api):
        mock_api.return_value = {
            "error": "gh: Forbidden (HTTP 403)",
            "error_type": "auth",
            "status_code": 403,
        }

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._add_sync_workflow_result(
                "me/forked-repo",
                "main",
                dry_run=True,
            )

        self.assertEqual(result, sync_forks.RESULT_ERROR)
        self.assertIn("Cannot verify existing sync-fork.yml", captured.getvalue())
        self.assertEqual(mock_api.call_count, 1)

    @patch.object(sync_forks, "gh_api")
    def test_missing_workflow_can_be_added_in_dry_run(self, mock_api):
        mock_api.return_value = {
            "error": "gh: Not Found (HTTP 404)",
            "error_type": "not_found",
            "status_code": 404,
        }

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._add_sync_workflow_result(
                "me/forked-repo",
                "main",
                dry_run=True,
            )

        self.assertEqual(result, sync_forks.RESULT_OK)
        self.assertIn("Would add sync-fork.yml", captured.getvalue())

    @patch.object(sync_forks, "gh_api")
    def test_lookup_encodes_branch_ref(self, mock_api):
        mock_api.return_value = {
            "error": "gh: Not Found (HTTP 404)",
            "error_type": "not_found",
            "status_code": 404,
        }

        with contextlib.redirect_stdout(io.StringIO()):
            result = sync_forks._add_sync_workflow_result(
                "me/forked-repo",
                "feature/branch",
                dry_run=True,
            )

        self.assertEqual(result, sync_forks.RESULT_OK)
        mock_api.assert_any_call(
            "repos/me/forked-repo/contents/.github/workflows/sync-fork.yml"
            "?ref=feature%2Fbranch"
        )

    @patch.object(sync_forks, "gh_api")
    def test_invalid_branch_fails_without_api_call(self, mock_api):
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._add_sync_workflow_result(
                "me/forked-repo",
                "",
                dry_run=True,
            )

        self.assertEqual(result, sync_forks.RESULT_ERROR)
        self.assertIn("invalid repo or branch", captured.getvalue())
        mock_api.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    def test_invalid_update_response_fails(self, mock_api):
        mock_api.side_effect = [
            {
                "error": "gh: Not Found (HTTP 404)",
                "error_type": "not_found",
                "status_code": 404,
            },
            [],
        ]

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._add_sync_workflow_result(
                "me/forked-repo",
                "main",
            )

        self.assertEqual(result, sync_forks.RESULT_ERROR)
        self.assertIn("Invalid workflow update response", captured.getvalue())


# ── get_file_count ────────────────────────────────────────────

class TestForkMetadataHelpers(unittest.TestCase):
    def test_get_compare_head_uses_owner_only(self):
        self.assertEqual(
            get_compare_head("upstream-owner/repo", "main"),
            "upstream-owner:main",
        )

    def test_encode_compare_ref_preserves_owner_separator(self):
        self.assertEqual(
            encode_compare_ref("upstream-owner:feature/branch"),
            "upstream-owner:feature%2Fbranch",
        )

    def test_encode_url_component_encodes_slashes(self):
        self.assertEqual(
            encode_url_component("release/2026-q2"),
            "release%2F2026-q2",
        )

    def test_build_compare_endpoint_encodes_branch_slashes(self):
        self.assertEqual(
            build_compare_endpoint(
                "me/forked-repo",
                "release/2026-q2",
                "upstream-owner:feature/branch",
            ),
            (
                "repos/me/forked-repo/compare/"
                "release%2F2026-q2...upstream-owner:feature%2Fbranch"
            ),
        )

    def test_get_compare_head_rejects_invalid_input(self):
        self.assertIsNone(get_compare_head("missing-slash", "main"))
        self.assertIsNone(get_compare_head("upstream-owner/repo", ""))

    def test_build_fork_record_adds_parent_owner(self):
        record = build_fork_record({
            "fork": True,
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "default_branch": "main",
            "parent": {
                "full_name": "upstream-owner/forked-repo",
                "default_branch": "develop",
            },
        })
        self.assertEqual(record["parent"], "upstream-owner/forked-repo")
        self.assertEqual(record["parent_branch"], "develop")
        self.assertEqual(record["parent_owner"], "upstream-owner")

    def test_build_fork_record_rejects_non_forks(self):
        self.assertIsNone(build_fork_record({"fork": False}))

    def test_build_fork_record_handles_malformed_parent(self):
        record = build_fork_record({
            "fork": True,
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "default_branch": "main",
            "parent": "not-a-dict",
        })

        self.assertEqual(record["parent"], "unknown")
        self.assertEqual(record["parent_branch"], "main")
        self.assertIsNone(record["parent_owner"])

    def test_build_fork_record_defaults_empty_parent_fields(self):
        record = build_fork_record({
            "fork": True,
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "default_branch": "main",
            "parent": {
                "full_name": None,
                "default_branch": None,
            },
        })

        self.assertEqual(record["parent"], "unknown")
        self.assertEqual(record["parent_branch"], "main")
        self.assertIsNone(record["parent_owner"])

    def test_parse_nonnegative_int_accepts_int_like_values(self):
        self.assertEqual(parse_nonnegative_int(0), 0)
        self.assertEqual(parse_nonnegative_int("42"), 42)

    def test_parse_nonnegative_int_rejects_malformed_values(self):
        self.assertIsNone(parse_nonnegative_int(True))
        self.assertIsNone(parse_nonnegative_int(False))
        self.assertIsNone(parse_nonnegative_int("-1"))
        self.assertIsNone(parse_nonnegative_int("not-int"))
        self.assertIsNone(parse_nonnegative_int(None))


class TestGhApiErrors(unittest.TestCase):
    def test_classify_rate_limit_as_retryable(self):
        result = gh_utils._classify_gh_error("gh: API rate limit exceeded (HTTP 403)")
        self.assertEqual(result["error_type"], "rate_limit")
        self.assertEqual(result["error_code"], "E_GH_RATE_LIMIT")
        self.assertEqual(result["status_code"], 403)
        self.assertTrue(result["retryable"])

    def test_classify_http_429_as_rate_limit(self):
        result = gh_utils._classify_gh_error("gh: Too Many Requests (HTTP 429)")
        self.assertEqual(result["error_type"], "rate_limit")
        self.assertEqual(result["error_code"], "E_GH_RATE_LIMIT")
        self.assertEqual(result["status_code"], 429)
        self.assertTrue(result["retryable"])

    def test_classify_not_found_as_non_retryable(self):
        result = gh_utils._classify_gh_error("gh: Not Found (HTTP 404)")
        self.assertEqual(result["error_type"], "not_found")
        self.assertEqual(result["error_code"], "E_GH_NOT_FOUND")
        self.assertEqual(result["status_code"], 404)
        self.assertFalse(result["retryable"])

    def test_classify_gh_login_hint_as_auth_error(self):
        result = gh_utils._classify_gh_error(
            "To get started with GitHub CLI, please run: gh auth login"
        )

        self.assertEqual(result["error_type"], "auth")
        self.assertEqual(result["error_code"], "E_GH_AUTH")
        self.assertFalse(result["retryable"])

    @patch("gh_utils.time.sleep", return_value=None)
    @patch("gh_utils.subprocess.run")
    def test_gh_api_retries_transient_server_error(self, mock_run, _mock_sleep):
        mock_run.side_effect = [
            Mock(returncode=1, stderr="gh: Bad Gateway (HTTP 502)", stdout=""),
            Mock(returncode=0, stderr="", stdout='{"ok": true}'),
        ]

        result = gh_utils.gh_api("repos/me/example")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_run.call_count, 2)

    @patch("gh_utils.time.sleep", return_value=None)
    @patch("gh_utils.subprocess.run")
    def test_gh_api_timeout_reports_structured_error(self, mock_run, _mock_sleep):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=gh_utils.API_TIMEOUT)

        result = gh_utils.gh_api("repos/me/example", timeout=1)

        self.assertEqual(result["error_type"], "timeout")
        self.assertEqual(result["error_code"], "E_GH_TIMEOUT")
        self.assertTrue(result["retryable"])

    @patch("gh_utils.subprocess.run")
    def test_gh_api_invalid_json_reports_structured_error(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="<html>gateway</html>")

        result = gh_utils.gh_api("repos/me/example")

        self.assertEqual(result["error_type"], "invalid_response")
        self.assertEqual(result["error_code"], "E_GH_INVALID_RESPONSE")
        self.assertIn("repos/me/example", result["error"])
        self.assertIn("<html>gateway</html>", result["detail"])

    @patch("gh_utils.subprocess.run")
    def test_gh_api_sends_empty_payload(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stderr="", stdout='{"ok": true}')

        result = gh_utils.gh_api("repos/me/example/dispatches", method="POST", payload={})

        self.assertEqual(result, {"ok": True})
        self.assertIn("--input", mock_run.call_args.args[0])
        self.assertEqual(mock_run.call_args.kwargs["input"], "{}")

    @patch.object(gh_utils, "GH", "gh")
    @patch("gh_utils.subprocess.run")
    def test_gh_api_os_error_reports_environment_error(self, mock_run):
        mock_run.side_effect = OSError("permission denied")

        result = gh_utils.gh_api("repos/me/example")

        self.assertEqual(result["error_type"], "environment")
        self.assertEqual(result["error_code"], "E_GH_ENVIRONMENT")
        self.assertFalse(result["retryable"])
        self.assertIn("Cannot execute gh CLI", result["error"])


class TestAuthDetection(unittest.TestCase):
    @patch.dict("os.environ", {"GH_TOKEN": "token-value"}, clear=False)  # nosec
    def test_detect_auth_mode_prefers_gh_token_env(self):
        result = gh_utils.detect_auth_mode()
        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "env_token")
        self.assertEqual(result["source"], "GH_TOKEN")

    @patch.dict("os.environ", {"GH_TOKEN": "  ", "GITHUB_TOKEN": ""}, clear=True)
    @patch("gh_utils.subprocess.run")
    def test_detect_auth_mode_ignores_blank_env_tokens(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        result = gh_utils.detect_auth_mode()

        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "gh_login")

    @patch.dict("os.environ", {}, clear=True)
    @patch("gh_utils.subprocess.run")
    def test_detect_auth_mode_uses_gh_login_when_available(self, mock_run):
        mock_run.return_value = Mock(returncode=0, stderr="", stdout="")

        result = gh_utils.detect_auth_mode()

        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "gh_login")
        self.assertEqual(result["source"], "gh auth login")

    @patch.dict("os.environ", {}, clear=True)
    @patch("gh_utils.subprocess.run")
    def test_detect_auth_mode_reports_unconfigured(self, mock_run):
        mock_run.return_value = Mock(returncode=1, stderr="not logged in", stdout="")

        result = gh_utils.detect_auth_mode()

        self.assertFalse(result["configured"])
        self.assertEqual(result["mode"], "unconfigured")

    @patch.object(gh_utils, "GH", "gh")
    @patch.dict("os.environ", {}, clear=True)
    @patch("gh_utils.subprocess.run")
    def test_detect_auth_mode_reports_gh_launch_failure(self, mock_run):
        mock_run.side_effect = OSError("permission denied")

        result = gh_utils.detect_auth_mode()

        self.assertFalse(result["configured"])
        self.assertEqual(result["mode"], "unavailable")
        self.assertIn("OSError", result["source"])

    @patch("gh_utils.gh_api")
    @patch("gh_utils.detect_auth_mode")
    def test_get_auth_context_reports_unconfigured_as_auth_error(
        self, mock_detect_auth_mode, mock_gh_api
    ):
        mock_detect_auth_mode.return_value = {
            "configured": False,
            "mode": "unconfigured",
            "source": None,
        }
        mock_gh_api.return_value = {
            "error": "run gh auth login",
            "error_type": "unknown",
            "error_code": "E_GH_UNKNOWN",
        }

        with self.assertRaises(gh_utils.AuthContextError) as ctx:
            gh_utils.get_auth_context()

        self.assertEqual(ctx.exception.error_type, "auth")
        self.assertIn("Not authenticated", ctx.exception.message)


class TestWindowsProcessEnvironment(unittest.TestCase):
    def test_ensure_windows_process_environment_restores_required_vars(self):
        with patch.object(gh_utils.os, "name", "nt"):
            with patch.dict(
                gh_utils.os.environ,
                {
                    "USERPROFILE": r"C:\Users\sciman",
                    "PATH": "",
                },
                clear=True,
            ):
                repaired = gh_utils.ensure_windows_process_environment()

                self.assertEqual(gh_utils.os.environ["SystemRoot"], r"C:\Windows")
                self.assertEqual(gh_utils.os.environ["WINDIR"], r"C:\Windows")
                self.assertEqual(
                    gh_utils.os.environ["ComSpec"],
                    r"C:\Windows\System32\cmd.exe",
                )
                self.assertEqual(
                    gh_utils.os.environ["APPDATA"],
                    r"C:\Users\sciman\AppData\Roaming",
                )
                self.assertEqual(
                    gh_utils.os.environ["LOCALAPPDATA"],
                    r"C:\Users\sciman\AppData\Local",
                )
                self.assertEqual(
                    gh_utils.os.environ["PROGRAMDATA"],
                    r"C:\ProgramData",
                )
                self.assertEqual(
                    gh_utils.os.environ["ProgramFiles"],
                    r"C:\Program Files",
                )
                self.assertIn("SystemRoot", repaired)

    def test_ensure_windows_process_environment_imports_codex_proxy_policy(self):
        with patch.object(
            gh_utils,
            "_read_codex_shell_environment_policy_set",
            return_value={
                "HTTP_PROXY": "http://127.0.0.1:10808",
                "HTTPS_PROXY": "http://127.0.0.1:10808",
                "NO_PROXY": "localhost,127.0.0.1,::1",
            },
        ):
            with patch.object(gh_utils.os, "name", "nt"):
                with patch.dict(
                    gh_utils.os.environ,
                    {
                        "USERPROFILE": r"C:\Users\sciman",
                        "PATH": "",
                    },
                    clear=True,
                ):
                    repaired = gh_utils.ensure_windows_process_environment()

                    self.assertEqual(
                        gh_utils.os.environ["HTTP_PROXY"],
                        "http://127.0.0.1:10808",
                    )
                    self.assertEqual(
                        gh_utils.os.environ["HTTPS_PROXY"],
                        "http://127.0.0.1:10808",
                    )
                    self.assertEqual(
                        gh_utils.os.environ["NO_PROXY"],
                        "localhost,127.0.0.1,::1",
                    )
                    self.assertNotIn("ANTHROPIC_AUTH_TOKEN", gh_utils.os.environ)
                    self.assertIn("HTTP_PROXY", repaired)

    def test_windows_environment_scripts_cover_required_variables(self):
        required = (
            "SystemRoot",
            "WINDIR",
            "ComSpec",
            "APPDATA",
            "LOCALAPPDATA",
            "PROGRAMDATA",
            "ProgramFiles",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        )
        script_paths = (
            os.path.join(_here, "scripts", "Initialize-WindowsProcessEnvironment.ps1"),
            os.path.join(_here, "scripts", "Initialize-WindowsProcessEnvironment.cmd"),
            os.path.join(_here, "scripts", "Verify-WindowsProcessEnvironment.ps1"),
        )

        for script_path in script_paths:
            with self.subTest(script=os.path.basename(script_path)):
                with open(script_path, encoding="utf-8") as handle:
                    content = handle.read()
                for name in required:
                    self.assertIn(name, content)


class TestGovernedClaudeHook(unittest.TestCase):
    def test_blocks_bare_windows_powershell_command(self):
        self.assertTrue(claude_hook._uses_direct_windows_powershell("powershell"))
        self.assertTrue(claude_hook._uses_direct_windows_powershell("powershell.exe"))

    def test_blocks_quoted_windows_powershell_path(self):
        command = (
            '& "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" '
            "-NoProfile"
        )
        self.assertTrue(claude_hook._uses_direct_windows_powershell(command))

    def test_allows_pwsh_command(self):
        self.assertFalse(
            claude_hook._uses_direct_windows_powershell(
                "pwsh -NoProfile -Command Get-Location"
            )
        )

    def test_blocks_sensitive_read_paths(self):
        self.assertTrue(claude_hook._is_sensitive_path("C:/tmp/.env"))
        self.assertTrue(claude_hook._is_sensitive_path("C:/tmp/id_ed25519.pub"))
        self.assertFalse(claude_hook._is_sensitive_path("docs/readme.md"))


# ── get_file_count ────────────────────────────────────────────

class TestGetFileCount(unittest.TestCase):
    @patch("gh_utils.gh_api")
    def test_counts_blobs_only(self, mock_api):
        mock_api.return_value = {
            "tree": [
                {"type": "blob", "path": "a.py"},
                {"type": "blob", "path": "b.py"},
                {"type": "tree", "path": "src"},
            ]
        }
        self.assertEqual(get_file_count("repo", "main"), 2)

    @patch("gh_utils.gh_api")
    def test_branch_ref_is_encoded(self, mock_api):
        mock_api.return_value = {"tree": []}

        self.assertEqual(get_file_count("repo", "feature/branch"), 0)

        mock_api.assert_any_call(
            "repos/repo/git/trees/feature%2Fbranch?recursive=1"
        )

    @patch("gh_utils.gh_api")
    def test_missing_branch_returns_none_without_api_call(self, mock_api):
        self.assertIsNone(get_file_count("repo", ""))
        mock_api.assert_not_called()

    @patch("gh_utils.gh_api")
    def test_error_returns_none(self, mock_api):
        mock_api.return_value = {"error": "404"}
        self.assertIsNone(get_file_count("repo", "main"))

    @patch("gh_utils.gh_api")
    def test_truncated_returns_none(self, mock_api):
        mock_api.return_value = {
            "tree": [{"type": "blob", "path": "a.py"}],
            "truncated": True,
        }
        self.assertIsNone(get_file_count("repo", "main"))

    @patch("gh_utils.gh_api")
    def test_empty_tree(self, mock_api):
        mock_api.return_value = {"tree": []}
        self.assertEqual(get_file_count("repo", "main"), 0)

    @patch("gh_utils.gh_api")
    def test_non_dict_tree_response_returns_none(self, mock_api):
        mock_api.return_value = []
        self.assertIsNone(get_file_count("repo", "main"))

    @patch("gh_utils.gh_api")
    def test_malformed_tree_returns_none(self, mock_api):
        mock_api.return_value = {"tree": "not-a-list"}
        self.assertIsNone(get_file_count("repo", "main"))


# ── safety_check ──────────────────────────────────────────────

class TestSafetyCheck(unittest.TestCase):
    def test_force_bypasses_check(self):
        result = sync_forks.safety_check("f", "main", "u", "main", force=True)
        self.assertTrue(result)

    @patch.object(sync_forks, "get_file_count")
    def test_fork_count_unknown_aborts(self, mock_gfc):
        mock_gfc.return_value = None
        self.assertFalse(
            sync_forks.safety_check("f", "main", "u", "main")
        )

    @patch.object(sync_forks, "get_file_count")
    def test_upstream_unknown_returns_skip(self, mock_gfc):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else None
        self.assertIsNone(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_file_threshold_abort(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 20
        result = sync_forks.safety_check("f", "main", "u/repo", "main")
        self.assertFalse(result)
        mock_api.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_deletion_limit_abort(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 95
        mock_api.return_value = {"total_deletions": 200, "files": []}
        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_critical_file_deletion_abort(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 95
        mock_api.return_value = {
            "total_deletions": 1,
            "files": [{"filename": "README.md", "status": "removed"}],
        }
        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_github_path_deletion_abort(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 95
        mock_api.return_value = {
            "total_deletions": 1,
            "files": [{"filename": ".github/workflows/ci.yml", "status": "removed"}],
        }
        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_safe_sync(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {
            "total_deletions": 3,
            "files": [{"filename": "old.txt", "status": "removed"}],
        }
        self.assertTrue(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_compare_error_aborts(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {"error": "compare failed"}
        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_malformed_deletion_count_aborts(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {"total_deletions": "many", "files": []}

        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_boolean_deletion_count_aborts(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {"total_deletions": True, "files": []}

        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_malformed_files_payload_aborts(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {"total_deletions": 0, "files": "not-a-list"}

        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_removed_filename_malformed_aborts(self, mock_gfc, mock_api):
        mock_gfc.side_effect = lambda repo, _: 100 if repo == "f" else 105
        mock_api.return_value = {
            "total_deletions": 1,
            "files": [{"status": "removed", "filename": None}],
        }

        self.assertFalse(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_exact_threshold_passes(self, mock_gfc, mock_api):
        # 50 files * 0.5 = 25 threshold, upstream=25 should pass
        mock_gfc.side_effect = lambda repo, _: 50 if repo == "f" else 25
        mock_api.return_value = {"total_deletions": 0, "files": []}
        self.assertTrue(
            sync_forks.safety_check("f", "main", "u/repo", "main")
        )

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "get_file_count")
    def test_just_below_threshold_aborts(self, mock_gfc, mock_api):
        # 50 files * 0.5 = 25 threshold, upstream=24 should abort
        mock_gfc.side_effect = lambda repo, _: 50 if repo == "f" else 24
        result = sync_forks.safety_check("f", "main", "u/repo", "main")
        self.assertFalse(result)
        mock_api.assert_not_called()


class TestSyncNowResult(unittest.TestCase):
    @patch.object(sync_forks, "gh_api")
    def test_restore_point_invalid_head_response_aborts(self, mock_api):
        mock_api.return_value = []

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._create_restore_point("me/fork", "main")

        self.assertFalse(result)
        self.assertIn("Cannot read current fork head", captured.getvalue())

    @patch.object(sync_forks, "gh_api")
    def test_restore_point_invalid_update_response_aborts(self, mock_api):
        mock_api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            [],
        ]

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._create_restore_point("me/fork", "main")

        self.assertFalse(result)
        self.assertIn("Cannot update restore point branch", captured.getvalue())

    @patch.object(sync_forks, "gh_api")
    def test_restore_point_invalid_create_response_aborts(self, mock_api):
        mock_api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            {
                "error": "gh: Not Found (HTTP 404)",
                "error_type": "not_found",
                "status_code": 404,
            },
            [],
        ]

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._create_restore_point("me/fork", "main")

        self.assertFalse(result)
        self.assertIn("Cannot create restore point branch", captured.getvalue())

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "safety_check")
    def test_sync_creates_restore_point_before_merge(self, mock_safety, mock_api):
        mock_safety.return_value = sync_forks.SAFE
        mock_api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            {"ref": "refs/heads/backup/pre-sync"},
            {"message": "merged"},
        ]

        result = sync_forks._sync_now_result("me/fork", "main", "upstream/repo", "main")

        self.assertEqual(result, sync_forks.RESULT_OK)
        self.assertEqual(mock_api.call_args_list[0].args[0], "repos/me/fork/git/ref/heads/main")
        self.assertEqual(mock_api.call_args_list[1].args[0], "repos/me/fork/git/refs/heads/backup/pre-sync")
        self.assertEqual(mock_api.call_args_list[2].args[0], "repos/me/fork/merge-upstream")

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "safety_check")
    def test_stable_source_only_repo_skips_default_branch_sync(
        self, mock_safety, mock_api
    ):
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._sync_now_result(
                "sciman-top/cockpit-tools",
                "main",
                "jlcodes99/cockpit-tools",
                "main",
            )

        self.assertEqual(result, sync_forks.RESULT_OK)
        self.assertIn("stable-source-only policy", captured.getvalue())
        mock_safety.assert_not_called()
        mock_api.assert_not_called()

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "safety_check")
    def test_invalid_merge_response_returns_error(self, mock_safety, mock_api):
        mock_safety.return_value = sync_forks.SAFE
        mock_api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            {"ref": "refs/heads/backup/pre-sync"},
            [],
        ]

        with contextlib.redirect_stdout(io.StringIO()) as captured:
            result = sync_forks._sync_now_result(
                "me/fork",
                "main",
                "upstream/repo",
                "main",
            )

        self.assertEqual(result, sync_forks.RESULT_ERROR)
        self.assertIn("Invalid merge-upstream response", captured.getvalue())

    @patch.object(sync_forks, "gh_api")
    @patch.object(sync_forks, "safety_check")
    def test_restore_point_failure_aborts_sync(self, mock_safety, mock_api):
        mock_safety.return_value = sync_forks.SAFE
        mock_api.side_effect = [
            {"object": {"sha": "0123456789abcdef"}},
            {"error": "gh: Bad Gateway (HTTP 502)"},
        ]

        result = sync_forks._sync_now_result("me/fork", "main", "upstream/repo", "main")

        self.assertEqual(result, sync_forks.RESULT_SAFETY)
        self.assertTrue(all(
            call.args[0] != "repos/me/fork/merge-upstream"
            for call in mock_api.call_args_list
        ))


# ── paginate ──────────────────────────────────────────────────

class TestPaginate(unittest.TestCase):
    @patch("gh_utils.gh_api")
    def test_single_page(self, mock_api):
        mock_api.return_value = [{"name": "r1"}, {"name": "r2"}]
        result = paginate("users/u/repos")
        self.assertEqual(len(result), 2)

    @patch("gh_utils.gh_api")
    def test_empty_response(self, mock_api):
        mock_api.return_value = []
        self.assertEqual(paginate("users/u/repos"), [])

    @patch("gh_utils.gh_api")
    def test_error_stops(self, mock_api):
        mock_api.return_value = {"error": "403 rate limit"}
        self.assertEqual(paginate("users/u/repos"), [])

    @patch("gh_utils.gh_api")
    def test_two_pages(self, mock_api):
        page1 = [{"name": f"r{i}"} for i in range(100)]
        page2 = [{"name": f"r{i}"} for i in range(100, 130)]
        mock_api.side_effect = [page1, page2]
        result = paginate("users/u/repos")
        self.assertEqual(len(result), 130)

    @patch("gh_utils.gh_api")
    def test_non_list_stops(self, mock_api):
        mock_api.return_value = "unexpected string"
        self.assertEqual(paginate("endpoint"), [])

    @patch("gh_utils.gh_api")
    def test_include_error_returns_error_payload(self, mock_api):
        mock_api.return_value = {
            "error": "403 rate limit",
            "error_type": "rate_limit",
            "retryable": True,
        }

        items, error = paginate("users/u/repos", include_error=True)

        self.assertEqual(items, [])
        self.assertEqual(error["error_type"], "rate_limit")

    @patch("gh_utils.gh_api")
    def test_include_error_returns_invalid_response_for_non_list(self, mock_api):
        mock_api.return_value = "unexpected string"

        items, error = paginate("users/u/repos", include_error=True)

        self.assertEqual(items, [])
        self.assertEqual(error["error_type"], "invalid_response")

    @patch("gh_utils.gh_api")
    def test_include_error_returns_invalid_response_for_non_error_dict(self, mock_api):
        mock_api.return_value = {"name": "not-a-list-page"}

        items, error = paginate("users/u/repos", include_error=True)

        self.assertEqual(items, [])
        self.assertEqual(error["error_type"], "invalid_response")

    @patch("gh_utils.gh_api")
    def test_include_error_reports_pagination_limit(self, mock_api):
        page = [{"name": f"r{i}"} for i in range(100)]
        mock_api.side_effect = [page, page]

        with patch.object(gh_utils, "MAX_PAGES", 2):
            items, error = paginate("users/u/repos", include_error=True)

        self.assertEqual(len(items), 200)
        self.assertEqual(error["error_type"], "invalid_response")
        self.assertIn("Pagination limit exceeded", error["error"])


class TestForkDiscovery(unittest.TestCase):
    @patch("gh_utils.gh_api")
    def test_get_fork_repos_uses_authenticated_owner_repo_endpoint(self, mock_api):
        mock_api.return_value = []

        result = gh_utils.get_fork_repos("me")

        self.assertEqual(result, [])
        self.assertTrue(mock_api.call_args_list)
        self.assertTrue(
            mock_api.call_args_list[0].args[0].startswith(
                "user/repos?affiliation=owner&visibility=all&"
            )
        )

    @patch("gh_utils.utc_now_iso")
    @patch("gh_utils.gh_api")
    def test_get_fork_repos_with_evidence_reports_live_source(
        self, mock_api, mock_now
    ):
        mock_now.return_value = "2026-04-25T10:00:00Z"
        mock_api.return_value = [{
            "fork": True,
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "default_branch": "main",
            "parent": {
                "full_name": "upstream-owner/forked-repo",
                "default_branch": "main",
            },
        }]

        forks, evidence = get_fork_repos_with_evidence("me")

        self.assertEqual(len(forks), 1)
        self.assertEqual(evidence["source"], "live_github_api")
        self.assertEqual(evidence["repo_count"], 1)
        self.assertEqual(evidence["fork_count"], 1)
        self.assertEqual(evidence["fetched_at"], "2026-04-25T10:00:00Z")
        self.assertEqual(evidence["parent_detail_lookup_count"], 0)
        self.assertEqual(evidence["parent_detail_cache_hit_count"], 0)
        self.assertEqual(
            evidence["endpoint"],
            gh_utils.FORK_DISCOVERY_ENDPOINT,
        )

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_raises_on_initial_discovery_error(self, mock_api):
        mock_api.return_value = {
            "error": "gateway timeout",
            "error_type": "timeout",
            "retryable": True,
        }

        with self.assertRaises(gh_utils.ForkDiscoveryError) as ctx:
            gh_utils.get_fork_repos("me")

        self.assertIn("Failed to list fork repositories", str(ctx.exception))

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_raises_on_partial_discovery_error(self, mock_api):
        first_page = []
        for i in range(100):
            first_page.append({
                "fork": True,
                "name": f"forked-repo-{i}",
                "full_name": f"me/forked-repo-{i}",
                "default_branch": "main",
                "parent": {
                    "full_name": f"upstream-owner/forked-repo-{i}",
                    "default_branch": "main",
                },
            })
        mock_api.side_effect = [
            first_page,
            {
                "error": "gateway timeout",
                "error_type": "timeout",
                "retryable": True,
            },
        ]

        with self.assertRaises(gh_utils.ForkDiscoveryError):
            gh_utils.get_fork_repos("me")

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_raises_when_parent_detail_lookup_fails(self, mock_api):
        mock_api.side_effect = [
            [{
                "fork": True,
                "name": "forked-repo",
                "full_name": "me/forked-repo",
                "default_branch": "main",
            }],
            {
                "error": "gh: Forbidden (HTTP 403)",
                "error_type": "auth",
                "status_code": 403,
            },
        ]

        with self.assertRaises(gh_utils.ForkDiscoveryError) as ctx:
            gh_utils.get_fork_repos("me")

        self.assertIn("Failed to inspect fork repository metadata", str(ctx.exception))
        self.assertEqual(ctx.exception.detail["repo"], "me/forked-repo")

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_raises_when_detail_lacks_parent(self, mock_api):
        mock_api.side_effect = [
            [{
                "fork": True,
                "name": "forked-repo",
                "full_name": "me/forked-repo",
                "default_branch": "main",
            }],
            {
                "fork": True,
                "name": "forked-repo",
                "full_name": "me/forked-repo",
                "default_branch": "main",
            },
        ]

        with self.assertRaises(gh_utils.ForkDiscoveryError) as ctx:
            gh_utils.get_fork_repos("me")

        self.assertIn("missing parent information", str(ctx.exception))
        self.assertEqual(ctx.exception.detail["repo"], "me/forked-repo")

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_caches_parent_detail_lookup(self, mock_api):
        mock_api.side_effect = [
            [
                {
                    "fork": True,
                    "name": "forked-repo",
                    "full_name": "me/forked-repo",
                    "default_branch": "main",
                },
                {
                    "fork": True,
                    "name": "forked-repo",
                    "full_name": "me/forked-repo",
                    "default_branch": "main",
                },
            ],
            {
                "fork": True,
                "name": "forked-repo",
                "full_name": "me/forked-repo",
                "default_branch": "main",
                "parent": {
                    "full_name": "upstream-owner/forked-repo",
                    "default_branch": "main",
                },
            },
        ]

        forks, evidence = get_fork_repos_with_evidence("me")

        self.assertEqual(len(forks), 2)
        self.assertEqual(evidence["parent_detail_lookup_count"], 1)
        self.assertEqual(evidence["parent_detail_cache_hit_count"], 1)
        self.assertEqual(mock_api.call_count, 2)

    @patch("gh_utils.gh_api")
    def test_get_fork_repos_skips_malformed_repo_without_full_name(self, mock_api):
        mock_api.return_value = [{
            "fork": True,
            "name": "forked-repo",
            "default_branch": "main",
            "parent": "not-a-dict",
        }]

        forks, evidence = get_fork_repos_with_evidence("me")

        self.assertEqual(forks, [])
        self.assertEqual(evidence["repo_count"], 1)
        self.assertEqual(evidence["fork_count"], 0)


class TestWorkflowContracts(unittest.TestCase):
    def test_integration_smoke_does_not_reference_secrets_in_if_conditions(self):
        workflow_path = os.path.join(
            _here, ".github", "workflows", "integration-api-smoke.yml"
        )
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = f.read()

        if_lines = [
            line.strip()
            for line in workflow.splitlines()
            if line.strip().startswith("if:")
        ]

        self.assertTrue(if_lines)
        self.assertTrue(all("secrets." not in line for line in if_lines))
        self.assertIn("id: integration_credentials", workflow)
        self.assertIn(
            "steps.integration_credentials.outputs.ready == 'true'",
            workflow,
        )
        self.assertIn("test_support.py", workflow)

    def test_generated_workflow_syncs_fork_default_branch(self):
        workflow = sync_forks._render_workflow_template()
        merge_lines = [
            line
            for line in workflow.splitlines()
            if "merge-upstream" in line and "-f branch=" in line
        ]

        self.assertEqual(len(merge_lines), 1)
        self.assertIn("github.event.repository.default_branch", merge_lines[0])
        self.assertNotIn("steps.upstream.outputs.branch", merge_lines[0])

    def test_generated_workflow_has_stable_source_guard(self):
        workflow = sync_forks._render_workflow_template()

        self.assertIn("Stable source policy guard", workflow)
        self.assertIn("STABLE_SOURCE_ONLY_REPOS", workflow)
        self.assertIn("sciman-top/cockpit-tools", workflow)
        self.assertIn("steps.stable_source_policy.outputs.skip != 'true'", workflow)

    def test_generated_workflow_url_encodes_compare_refs(self):
        workflow = sync_forks._render_workflow_template()

        self.assertIn("BASE_REF:", workflow)
        self.assertIn("HEAD_REF:", workflow)
        self.assertIn("urllib.parse import quote", workflow)
        self.assertIn("compare/${COMPARE_BASE}...${COMPARE_HEAD}", workflow)


class TestListForks(unittest.TestCase):
    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_help_returns_exit_ok_without_runtime_checks(
        self, mock_check, mock_auth_context
    ):
        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--help"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Usage:", captured.getvalue())
        mock_check.assert_not_called()
        mock_auth_context.assert_not_called()

    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_unknown_args_return_exit_error_without_runtime_checks(
        self, mock_check, mock_auth_context
    ):
        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--bad-flag"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Unknown argument", captured.getvalue())
        mock_check.assert_not_called()
        mock_auth_context.assert_not_called()

    def test_unknown_args_return_json_error_payload(self):
        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--json", "--bad-flag"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["tool"], "list-forks")
        self.assertIn("Unknown argument", payload["error"])
        self.assertEqual(payload["error_code"], "E_CLI_UNKNOWN_ARGUMENT")
        self.assertEqual(payload["error_type"], "cli")

    def test_unknown_args_return_json_when_json_flag_is_last(self):
        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--bad-flag", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["tool"], "list-forks")
        self.assertEqual(payload["error_code"], "E_CLI_UNKNOWN_ARGUMENT")

    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_json_output_handles_empty_repo_list(
        self, mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }

        discovery = {
            "source": "live_github_api",
            "endpoint": gh_utils.FORK_DISCOVERY_ENDPOINT,
            "fetched_at": "2026-04-25T10:00:00Z",
            "authenticated_user": "me",
            "repo_count": 5,
            "fork_count": 0,
        }
        with patch.object(
            list_forks,
            "get_fork_repos_with_evidence",
            return_value=([], discovery),
        ):
            with patch.object(list_forks.sys, "argv", ["list-forks.py", "--json"]):
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["summary"]["total"], 0)
        self.assertEqual(payload["auth"]["mode"], "env_token")
        self.assertEqual(payload["discovery"]["source"], "live_github_api")
        self.assertEqual(payload["discovery"]["repo_count"], 5)
        mock_check.assert_called_once()

    @patch.object(list_forks, "get_fork_repos_with_evidence")
    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_json_output_handles_fork_discovery_failure(
        self, _mock_check, mock_auth_context, mock_get_forks
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }
        mock_get_forks.side_effect = gh_utils.ForkDiscoveryError(
            message="Failed to list fork repositories from GitHub API.",
            detail={"error": "gateway timeout"},
        )

        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["tool"], "list-forks")
        self.assertEqual(payload["error"], "fork_discovery_failed")
        self.assertEqual(payload["error_code"], "E_FORK_DISCOVERY_FAILED")
        self.assertEqual(payload["error_type"], "discovery")

    @patch.object(list_forks, "gh_api")
    @patch.object(list_forks, "get_fork_repos_with_evidence")
    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_compare_uses_owner_branch_ref(
        self, _mock_check, mock_auth_context, mock_get_forks, mock_api
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        discovery = {
            "source": "live_github_api",
            "endpoint": gh_utils.FORK_DISCOVERY_ENDPOINT,
            "fetched_at": "2026-04-25T10:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
        }
        mock_get_forks.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "develop",
            "parent_owner": "upstream-owner",
        }], discovery)
        mock_api.side_effect = [
            {"content": "workflow"},
            {"ahead_by": 4, "behind_by": 0},
        ]

        with patch.object(list_forks.sys, "argv", ["list-forks.py"]):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = list_forks.main()

        self.assertEqual(exit_code, 0)
        mock_api.assert_any_call(
            "repos/me/forked-repo/compare/main...upstream-owner:develop"
        )

    @patch.object(list_forks, "gh_api")
    def test_compare_endpoint_encodes_branch_slashes(self, mock_api):
        fork = {
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "release/2026-q2",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "feature/branch",
            "parent_owner": "upstream-owner",
        }
        mock_api.side_effect = [
            {"content": "workflow"},
            {"ahead_by": 4, "behind_by": 0},
        ]

        rows = list_forks._collect_fork_statuses([fork])

        self.assertEqual(rows[0]["behind_by"], 4)
        mock_api.assert_any_call(
            "repos/me/forked-repo/contents/.github/workflows/sync-fork.yml"
            "?ref=release%2F2026-q2"
        )
        mock_api.assert_any_call(
            "repos/me/forked-repo/compare/"
            "release%2F2026-q2...upstream-owner:feature%2Fbranch"
        )

    @patch.object(list_forks, "gh_api")
    def test_collect_fork_statuses_uses_upstream_ahead_as_behind_count(
        self, mock_api
    ):
        fork = {
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }
        mock_api.side_effect = [
            {"content": "workflow"},
            {"ahead_by": 3, "behind_by": 0},
        ]

        rows = list_forks._collect_fork_statuses([fork])

        self.assertEqual(rows[0]["behind_by"], 3)

    @patch.object(list_forks, "gh_api")
    def test_collect_fork_statuses_ignores_malformed_ahead_by(self, mock_api):
        fork = {
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }
        mock_api.side_effect = [
            {"content": "workflow"},
            {"ahead_by": True, "behind_by": 0},
        ]

        rows = list_forks._collect_fork_statuses([fork])
        self.assertIsNone(rows[0]["behind_by"])

    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_json_output_treats_non_dict_workflow_response_as_missing(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }
        fork = {
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }

        discovery = {
            "source": "live_github_api",
            "endpoint": gh_utils.FORK_DISCOVERY_ENDPOINT,
            "fetched_at": "2026-04-25T10:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
        }
        with patch.object(
            list_forks,
            "get_fork_repos_with_evidence",
            return_value=([fork], discovery),
        ):
            with patch.object(list_forks, "gh_api", side_effect=[[], {"ahead_by": 0}]):
                with patch.object(list_forks.sys, "argv", ["list-forks.py", "--json"]):
                    with contextlib.redirect_stdout(io.StringIO()) as captured:
                        exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(payload["repos"][0]["workflow_installed"])
        self.assertEqual(payload["repos"][0]["workflow_status"], "Missing")

    @patch.object(list_forks, "get_auth_context")
    @patch.object(list_forks, "check_gh_cli")
    def test_json_output_returns_structured_auth_failure(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.side_effect = gh_utils.AuthContextError(
            auth_info={
                "configured": False,
                "mode": "unconfigured",
                "source": None,
            },
            error_type="auth",
            message="Not authenticated. Set GH_TOKEN/GITHUB_TOKEN or run: gh auth login",
        )

        with patch.object(list_forks.sys, "argv", ["list-forks.py", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = list_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["tool"], "list-forks")
        self.assertEqual(payload["error"], "authentication_failed")
        self.assertEqual(payload["error_code"], "E_AUTH_CONTEXT_FAILED")
        self.assertEqual(payload["error_type"], "auth")


class TestSyncForksMain(unittest.TestCase):
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_help_returns_exit_ok_without_runtime_checks(
        self, mock_check, mock_auth_context
    ):
        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--help"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_OK)
        self.assertIn("Usage:", captured.getvalue())
        mock_check.assert_not_called()
        mock_auth_context.assert_not_called()

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_unknown_option_returns_exit_error_without_runtime_checks(
        self, mock_check, mock_auth_context
    ):
        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--bad-flag"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertIn("Unknown option", captured.getvalue())
        mock_check.assert_not_called()
        mock_auth_context.assert_not_called()

    def test_unknown_option_returns_json_error_payload(self):
        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "--bad-flag"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["tool"], "sync-forks")
        self.assertIn("Unknown option", payload["error"])
        self.assertEqual(payload["error_code"], "E_CLI_UNKNOWN_OPTION")
        self.assertEqual(payload["error_type"], "cli")

    def test_unknown_option_returns_json_when_json_flag_is_last(self):
        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--bad-flag", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["tool"], "sync-forks")
        self.assertEqual(payload["error_code"], "E_CLI_UNKNOWN_OPTION")

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_force_sync_requires_explicit_confirmation(
        self, mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }

        with patch.dict("os.environ", {sync_forks.FORCE_SYNC_ACK_ENV: ""}, clear=False):
            with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "--force", "forked-repo"]):
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["error"], "force_confirmation_required")
        self.assertEqual(payload["error_code"], "E_FORCE_CONFIRMATION_REQUIRED")
        self.assertEqual(payload["error_type"], "safety")
        mock_check.assert_called_once()
        mock_auth_context.assert_called_once()

    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_force_add_workflow_requires_explicit_confirmation(
        self, _mock_check, mock_auth_context, mock_resolve
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }

        with patch.dict("os.environ", {sync_forks.FORCE_SYNC_ACK_ENV: ""}, clear=False):
            with patch.object(
                sync_forks.sys,
                "argv",
                ["sync-forks.py", "--json", "--add-workflow", "--force", "forked-repo"],
            ):
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["error"], "force_confirmation_required")
        self.assertEqual(payload["error_code"], "E_FORCE_CONFIRMATION_REQUIRED")
        self.assertEqual(payload["error_type"], "safety")
        mock_resolve.assert_not_called()

    @patch.object(sync_forks, "_sync_now_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_force_sync_with_confirmation_runs(
        self, _mock_check, mock_auth_context, mock_resolve, mock_sync
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_sync.return_value = sync_forks.RESULT_OK

        with patch.dict(
            "os.environ",
            {sync_forks.FORCE_SYNC_ACK_ENV: sync_forks.FORCE_SYNC_ACK_VALUE},
            clear=False,
        ):
            with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--force", "forked-repo"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_OK)
        mock_sync.assert_called_once()

    @patch.object(sync_forks, "_add_sync_workflow_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_force_add_workflow_with_confirmation_runs(
        self, _mock_check, mock_auth_context, mock_resolve, mock_add
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_add.return_value = sync_forks.RESULT_OK

        with patch.dict(
            "os.environ",
            {sync_forks.FORCE_SYNC_ACK_ENV: sync_forks.FORCE_SYNC_ACK_VALUE},
            clear=False,
        ):
            with patch.object(
                sync_forks.sys,
                "argv",
                ["sync-forks.py", "--add-workflow", "--force", "forked-repo"],
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_OK)
        mock_add.assert_called_once_with("me/forked-repo", "main", False, True)

    @patch.object(sync_forks, "_sync_now_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_json_output_returns_summary_payload(
        self, mock_check, mock_auth_context, mock_resolve, mock_sync
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "env_token",
                "source": "GH_TOKEN",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_sync.return_value = sync_forks.RESULT_OK

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "forked-repo"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_OK)
        self.assertEqual(payload["tool"], "sync-forks")
        self.assertEqual(payload["summary"]["success"], 1)
        self.assertEqual(payload["auth"]["source"], "GH_TOKEN")
        self.assertEqual(payload["discovery"]["source"], "explicit_args")
        self.assertEqual(payload["discovery"]["requested"], ["forked-repo"])
        mock_check.assert_called_once()

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_specific_repo_resolution_error_returns_exit_error(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        with patch.object(sync_forks, "gh_api", return_value={"error": "404"}):
            with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "missing"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_non_fork_target_returns_exit_error(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        repo = {
            "fork": False,
            "name": "plain-repo",
            "full_name": "me/plain-repo",
            "default_branch": "main",
        }
        with patch.object(sync_forks, "gh_api", return_value=repo):
            with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "plain-repo"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_invalid_specific_repo_response_returns_exit_error(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        with patch.object(sync_forks, "gh_api", return_value=[]):
            with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "broken"]):
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertIn("invalid repository response", captured.getvalue())

    @patch.object(sync_forks, "_add_sync_workflow_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_add_workflow_failure_returns_exit_error(
        self, _mock_check, mock_auth_context, mock_resolve, mock_add
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_add.return_value = sync_forks.RESULT_ERROR

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--add-workflow", "forked-repo"]):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)

    @patch.object(sync_forks, "_sync_now_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_sync_safety_abort_returns_exit_safety(
        self, _mock_check, mock_auth_context, mock_resolve, mock_sync
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_sync.return_value = sync_forks.RESULT_SAFETY

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "forked-repo"]):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_SAFETY)

    @patch.object(sync_forks, "_sync_now_result")
    @patch.object(sync_forks, "_resolve_specific_forks")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_sync_runtime_failure_returns_exit_error(
        self, _mock_check, mock_auth_context, mock_resolve, mock_sync
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_resolve.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], False)
        mock_sync.return_value = sync_forks.RESULT_ERROR

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "forked-repo"]):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)

    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_json_output_returns_structured_auth_failure(
        self, _mock_check, mock_auth_context
    ):
        mock_auth_context.side_effect = gh_utils.AuthContextError(
            auth_info={
                "configured": False,
                "mode": "unconfigured",
                "source": None,
            },
            error_type="auth",
            message="Not authenticated. Set GH_TOKEN/GITHUB_TOKEN or run: gh auth login",
        )

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["tool"], "sync-forks")
        self.assertEqual(payload["error"], "authentication_failed")
        self.assertEqual(payload["error_code"], "E_AUTH_CONTEXT_FAILED")
        self.assertEqual(payload["error_type"], "auth")

    @patch.object(sync_forks, "get_fork_repos_with_evidence")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_json_output_returns_structured_fork_discovery_failure(
        self, _mock_check, mock_auth_context, mock_get_forks
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_get_forks.side_effect = gh_utils.ForkDiscoveryError(
            message="Failed to list fork repositories from GitHub API.",
            detail={"error": "gateway timeout"},
        )

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)
        self.assertEqual(payload["tool"], "sync-forks")
        self.assertEqual(payload["error"], "fork_discovery_failed")
        self.assertEqual(payload["error_code"], "E_FORK_DISCOVERY_FAILED")
        self.assertEqual(payload["error_type"], "discovery")


# ── Named constants ───────────────────────────────────────────

class TestConstants(unittest.TestCase):
    def test_safe_abort_skip_values(self):
        self.assertIs(sync_forks.SAFE, True)
        self.assertIs(sync_forks.ABORT, False)
        self.assertIs(sync_forks.SKIP, None)

    def test_exit_codes(self):
        self.assertEqual(sync_forks.EXIT_OK, 0)
        self.assertEqual(sync_forks.EXIT_ERROR, 1)
        self.assertEqual(sync_forks.EXIT_SAFETY, 3)


class TestSyncForksHelpers(unittest.TestCase):
    def test_dedupe_preserve_order(self):
        self.assertEqual(
            sync_forks._dedupe_preserve_order(
                ["fork-a", "fork-b", "fork-a", "fork-c", "fork-b"]
            ),
            ["fork-a", "fork-b", "fork-c"],
        )

    def test_mode_write_classification_is_explicit(self):
        self.assertTrue(
            sync_forks._mode_writes_remotely(sync_forks.MODE_SYNC, dry_run=False)
        )
        self.assertTrue(
            sync_forks._mode_writes_remotely(
                sync_forks.MODE_ADD_WORKFLOW,
                dry_run=False,
            )
        )
        self.assertFalse(
            sync_forks._mode_writes_remotely(
                sync_forks.MODE_VERIFY_SCHEDULE,
                dry_run=False,
            )
        )
        self.assertFalse(
            sync_forks._mode_writes_remotely(
                sync_forks.MODE_VERIFY_RELEASES,
                dry_run=False,
            )
        )
        self.assertFalse(
            sync_forks._mode_writes_remotely(sync_forks.MODE_SYNC, dry_run=True)
        )
        with self.assertRaises(ValueError):
            sync_forks._mode_writes_remotely("new-mode", dry_run=False)

    def test_force_confirmation_uses_mode_write_classification(self):
        with patch.dict("os.environ", {sync_forks.FORCE_SYNC_ACK_ENV: ""}, clear=False):
            self.assertTrue(
                sync_forks._force_confirmation_missing(
                    True,
                    sync_forks.MODE_SYNC,
                    dry_run=False,
                )
            )
            self.assertTrue(
                sync_forks._force_confirmation_missing(
                    True,
                    sync_forks.MODE_ADD_WORKFLOW,
                    dry_run=False,
                )
            )
            self.assertFalse(
                sync_forks._force_confirmation_missing(
                    True,
                    sync_forks.MODE_VERIFY_SCHEDULE,
                    dry_run=False,
                )
            )
            self.assertFalse(
                sync_forks._force_confirmation_missing(
                    True,
                    sync_forks.MODE_VERIFY_RELEASES,
                    dry_run=False,
                )
            )
            self.assertFalse(
                sync_forks._force_confirmation_missing(
                    True,
                    sync_forks.MODE_SYNC,
                    dry_run=True,
                )
            )

    @patch.object(sync_forks, "gh_api")
    def test_verify_release_retention_accepts_latest_assets_only(self, mock_api):
        forks = [{
            "name": "cockpit-tools",
            "full_name": "sciman-top/cockpit-tools",
            "branch": "main",
            "parent": "jlcodes99/cockpit-tools",
            "parent_branch": "main",
        }]
        upstream_latest = {
            "tag_name": "v2",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "tool.msi"}],
        }
        fork_latest = {
            "tag_name": "v2",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "tool.msi"}],
        }
        fork_old = {
            "tag_name": "v1",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        mock_api.side_effect = [[upstream_latest], [fork_latest, fork_old]]

        rows, summary = sync_forks._verify_release_retention(forks)

        self.assertEqual(rows[0]["next_action"], "none")
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["latest_stable_assets_required"], 1)
        self.assertEqual(summary["old_stable_assets_present"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_release_retention_flags_old_stable_assets(self, mock_api):
        forks = [{
            "name": "cockpit-tools",
            "full_name": "sciman-top/cockpit-tools",
            "branch": "main",
            "parent": "jlcodes99/cockpit-tools",
            "parent_branch": "main",
        }]
        upstream_latest = {
            "tag_name": "v2",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "tool.exe"}],
        }
        fork_latest = {
            "tag_name": "v2",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "tool.exe"}],
        }
        fork_old = {
            "tag_name": "v1",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "old-tool.exe"}],
        }
        mock_api.side_effect = [[upstream_latest], [fork_latest, fork_old]]

        rows, summary = sync_forks._verify_release_retention(forks)

        self.assertEqual(rows[0]["next_action"], "prune_old_stable_release_assets")
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["old_stable_assets_present"], 1)

    @patch.object(sync_forks, "gh_api")
    def test_verify_release_retention_ignores_source_only_upstream(self, mock_api):
        forks = [{
            "name": "claude-dashboard",
            "full_name": "sciman-top/claude-dashboard",
            "branch": "main",
            "parent": "uppinote20/claude-dashboard",
            "parent_branch": "main",
        }]
        upstream_latest = {
            "tag_name": "v1",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        mock_api.side_effect = [[upstream_latest], []]

        rows, summary = sync_forks._verify_release_retention(forks)

        self.assertEqual(rows[0]["release_policy"], "source_only_release_notes")
        self.assertEqual(rows[0]["next_action"], "none")
        self.assertEqual(summary["latest_stable_assets_required"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_schedule_readiness_counts_missing_items(self, mock_api):
        forks = [{
            "name": "fork-a",
            "full_name": "me/fork-a",
            "branch": "main",
            "parent": "upstream/a",
            "parent_branch": "main",
        }]
        mock_api.side_effect = [
            {"error": "404", "error_type": "not_found", "status_code": 404},
            {"error": "404", "error_type": "not_found", "status_code": 404},
            {"workflow_runs": []},
        ]

        rows, summary = sync_forks._verify_schedule_readiness(forks)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["next_action"], "install_sync_workflow")
        self.assertFalse(rows[0]["workflow_installed"])
        self.assertFalse(rows[0]["secret_sync_pat_present"])
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["missing_workflow"], 1)
        self.assertEqual(summary["missing_secret"], 1)
        self.assertEqual(summary["scheduled_run_missing_or_unhealthy"], 1)
        self.assertEqual(summary["errors"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_schedule_readiness_counts_ready_repo(self, mock_api):
        forks = [{
            "name": "fork-a",
            "full_name": "me/fork-a",
            "branch": "main",
            "parent": "upstream/a",
            "parent_branch": "main",
        }]
        current_workflow = sync_forks._render_workflow_template()
        mock_api.side_effect = [
            {"content": _encode(current_workflow)},
            {"name": "SYNC_PAT"},
            {"workflow_runs": [{
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-29T00:00:00Z",
                "updated_at": "2026-04-29T00:01:00Z",
                "html_url": "https://example.com/run/1",
            }]},
        ]

        rows, summary = sync_forks._verify_schedule_readiness(forks)
        self.assertEqual(rows[0]["next_action"], "none")
        self.assertTrue(rows[0]["workflow_current"])
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["workflow_needs_update"], 0)
        self.assertEqual(summary["errors"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_schedule_readiness_flags_stale_workflow(self, mock_api):
        forks = [{
            "name": "fork-a",
            "full_name": "me/fork-a",
            "branch": "main",
            "parent": "upstream/a",
            "parent_branch": "main",
        }]
        stale_workflow = "name: Sync Fork\non:\n  push:\n"
        mock_api.side_effect = [
            {"content": _encode(stale_workflow)},
            {"name": "SYNC_PAT"},
            {"workflow_runs": [{
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-29T00:00:00Z",
                "updated_at": "2026-04-29T00:01:00Z",
                "html_url": "https://example.com/run/1",
            }]},
        ]

        rows, summary = sync_forks._verify_schedule_readiness(forks)
        self.assertTrue(rows[0]["workflow_installed"])
        self.assertFalse(rows[0]["workflow_current"])
        self.assertIn("workflow_dispatch", rows[0]["workflow_update_reason"])
        self.assertEqual(rows[0]["next_action"], "update_sync_workflow")
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["missing_workflow"], 0)
        self.assertEqual(summary["workflow_needs_update"], 1)
        self.assertEqual(summary["errors"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_schedule_readiness_handles_malformed_run_item(self, mock_api):
        forks = [{
            "name": "fork-a",
            "full_name": "me/fork-a",
            "branch": "main",
            "parent": "upstream/a",
            "parent_branch": "main",
        }]
        current_workflow = sync_forks._render_workflow_template()
        mock_api.side_effect = [
            {"content": _encode(current_workflow)},
            {"name": "SYNC_PAT"},
            {"workflow_runs": [None]},
            {"workflow_runs": []},
        ]

        rows, summary = sync_forks._verify_schedule_readiness(forks)
        self.assertEqual(rows[0]["next_action"], "wait_or_trigger_smoke_run")
        self.assertIsNone(rows[0]["last_scheduled_run"])
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["errors"], 0)

    @patch.object(sync_forks, "gh_api")
    def test_verify_schedule_readiness_accepts_successful_smoke_run(self, mock_api):
        forks = [{
            "name": "fork-a",
            "full_name": "me/fork-a",
            "branch": "main",
            "parent": "upstream/a",
            "parent_branch": "main",
        }]
        current_workflow = sync_forks._render_workflow_template()
        mock_api.side_effect = [
            {"content": _encode(current_workflow)},
            {"name": "SYNC_PAT"},
            {"workflow_runs": []},
            {"workflow_runs": [{
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-05-10T11:42:40Z",
                "updated_at": "2026-05-10T11:43:00Z",
                "html_url": "https://example.com/run/2",
            }]},
        ]

        rows, summary = sync_forks._verify_schedule_readiness(forks)
        self.assertEqual(rows[0]["next_action"], "none")
        self.assertIsNone(rows[0]["last_scheduled_run"])
        self.assertEqual(rows[0]["last_smoke_run"]["conclusion"], "success")
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["scheduled_run_missing_or_unhealthy"], 0)


class TestSyncForksVerifyScheduleMode(unittest.TestCase):
    @patch.object(sync_forks, "_verify_schedule_readiness")
    @patch.object(sync_forks, "get_fork_repos_with_evidence")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_verify_schedule_json_returns_exit_safety_when_not_ready(
        self, _mock_check, mock_auth_context, mock_get_forks, mock_verify
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_get_forks.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], {
            "source": "live_github_api",
            "endpoint": "user/repos?affiliation=owner&visibility=all",
            "fetched_at": "2026-04-29T00:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
            "parent_detail_lookup_count": 0,
            "parent_detail_cache_hit_count": 0,
        })
        mock_verify.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "upstream": "upstream-owner/forked-repo",
            "upstream_branch": "main",
            "workflow_installed": False,
            "secret_sync_pat_present": False,
            "last_scheduled_run": None,
            "result": sync_forks.RESULT_OK,
            "next_action": "install_sync_workflow",
        }], {
            "total": 1,
            "ready": 0,
            "missing_workflow": 1,
            "missing_secret": 1,
            "scheduled_run_missing_or_unhealthy": 1,
            "errors": 0,
        })

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "--verify-schedule"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_SAFETY)
        self.assertEqual(payload["mode"], "verify-schedule")
        self.assertEqual(payload["summary"]["ready"], 0)
        self.assertEqual(payload["summary"]["missing_workflow"], 1)

    @patch.object(sync_forks, "_verify_schedule_readiness")
    @patch.object(sync_forks, "get_fork_repos_with_evidence")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_verify_schedule_json_returns_exit_safety_when_workflow_needs_update(
        self, _mock_check, mock_auth_context, mock_get_forks, mock_verify
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_get_forks.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], {
            "source": "live_github_api",
            "endpoint": "user/repos?affiliation=owner&visibility=all",
            "fetched_at": "2026-04-29T00:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
            "parent_detail_lookup_count": 0,
            "parent_detail_cache_hit_count": 0,
        })
        mock_verify.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "upstream": "upstream-owner/forked-repo",
            "upstream_branch": "main",
            "workflow_installed": True,
            "workflow_current": False,
            "workflow_update_reason": "missing workflow_dispatch trigger",
            "secret_sync_pat_present": True,
            "last_scheduled_run": {
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-29T00:00:00Z",
                "updated_at": "2026-04-29T00:01:00Z",
                "html_url": "https://example.com/run/1",
            },
            "result": sync_forks.RESULT_OK,
            "next_action": "update_sync_workflow",
        }], {
            "total": 1,
            "ready": 0,
            "missing_workflow": 0,
            "workflow_needs_update": 1,
            "missing_secret": 0,
            "scheduled_run_missing_or_unhealthy": 0,
            "errors": 0,
        })

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "--verify-schedule"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(exit_code, sync_forks.EXIT_SAFETY)
        self.assertEqual(payload["summary"]["workflow_needs_update"], 1)

    @patch.object(sync_forks, "_verify_schedule_readiness")
    @patch.object(sync_forks, "get_fork_repos_with_evidence")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_verify_schedule_json_returns_exit_error_when_api_errors(
        self, _mock_check, mock_auth_context, mock_get_forks, mock_verify
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_get_forks.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], {
            "source": "live_github_api",
            "endpoint": "user/repos?affiliation=owner&visibility=all",
            "fetched_at": "2026-04-29T00:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
            "parent_detail_lookup_count": 0,
            "parent_detail_cache_hit_count": 0,
        })
        mock_verify.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "upstream": "upstream-owner/forked-repo",
            "upstream_branch": "main",
            "workflow_installed": False,
            "secret_sync_pat_present": False,
            "last_scheduled_run": None,
            "result": sync_forks.RESULT_ERROR,
            "next_action": "investigate_api_errors",
        }], {
            "total": 1,
            "ready": 0,
            "missing_workflow": 0,
            "missing_secret": 0,
            "scheduled_run_missing_or_unhealthy": 0,
            "errors": 1,
        })

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--json", "--verify-schedule"]):
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = sync_forks.main()

        self.assertEqual(exit_code, sync_forks.EXIT_ERROR)

    @patch.object(sync_forks, "_verify_schedule_readiness")
    @patch.object(sync_forks, "get_fork_repos_with_evidence")
    @patch.object(sync_forks, "get_auth_context")
    @patch.object(sync_forks, "check_gh_cli")
    def test_verify_schedule_text_prints_per_repo_next_action(
        self, _mock_check, mock_auth_context, mock_get_forks, mock_verify
    ):
        mock_auth_context.return_value = {
            "username": "me",
            "auth": {
                "configured": True,
                "mode": "gh_login",
                "source": "gh auth login",
            },
        }
        mock_get_forks.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "parent": "upstream-owner/forked-repo",
            "parent_branch": "main",
            "parent_owner": "upstream-owner",
        }], {
            "source": "live_github_api",
            "endpoint": "user/repos?affiliation=owner&visibility=all",
            "fetched_at": "2026-04-29T00:00:00Z",
            "authenticated_user": "me",
            "repo_count": 1,
            "fork_count": 1,
            "parent_detail_lookup_count": 0,
            "parent_detail_cache_hit_count": 0,
        })
        mock_verify.return_value = ([{
            "name": "forked-repo",
            "full_name": "me/forked-repo",
            "branch": "main",
            "upstream": "upstream-owner/forked-repo",
            "upstream_branch": "main",
            "workflow_installed": True,
            "workflow_current": False,
            "workflow_update_reason": "missing workflow_dispatch trigger",
            "secret_sync_pat_present": True,
            "last_scheduled_run": {
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-29T00:00:00Z",
                "updated_at": "2026-04-29T00:01:00Z",
                "html_url": "https://example.com/run/1",
            },
            "result": sync_forks.RESULT_OK,
            "next_action": "update_sync_workflow",
        }], {
            "total": 1,
            "ready": 0,
            "missing_workflow": 0,
            "workflow_needs_update": 1,
            "missing_secret": 0,
            "scheduled_run_missing_or_unhealthy": 0,
            "errors": 0,
        })

        with patch.object(sync_forks.sys, "argv", ["sync-forks.py", "--verify-schedule"]):
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                exit_code = sync_forks.main()

        output = captured.getvalue()
        self.assertEqual(exit_code, sync_forks.EXIT_SAFETY)
        self.assertIn("Schedule readiness details:", output)
        self.assertIn("me/forked-repo", output)
        self.assertIn("update_sync_workflow", output)


if __name__ == "__main__":
    unittest.main()
