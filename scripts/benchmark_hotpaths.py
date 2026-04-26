#!/usr/bin/env python3
"""
Synthetic benchmark for GitHub Toolkit hot paths.

This benchmark is network-free and uses mocked gh_api/auth calls to track the
CPU-side loop/serialization overhead for core paths.
"""

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

def _load_patch_factory():
    try:
        from unittest.mock import patch
        return patch
    except OSError:
        from test_support import patch
        return patch


patch = _load_patch_factory()

DEFAULT_SIZES = (10, 100, 500)
DEFAULT_RUNS = 5
MAX_MEAN_MS = {
    "collect_fork_statuses": {10: 200, 100: 1500, 500: 7000},
    "sync_all_report": {10: 200, 100: 1500, 500: 7000},
    "sync_specific_deduped": {10: 250, 100: 1800, 500: 8000},
}


def _load_module(alias, filename):
    module_path = os.path.join(ROOT_DIR, filename)
    spec = importlib.util.spec_from_file_location(alias, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIST_FORKS = _load_module("benchmark_list_forks", "list-forks.py")
SYNC_FORKS = _load_module("benchmark_sync_forks", "sync-forks.py")


def _parse_sizes(text):
    values = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.append(int(chunk))
    if not values:
        raise ValueError("sizes cannot be empty")
    if any(v <= 0 for v in values):
        raise ValueError("sizes must be positive integers")
    return tuple(values)


def _build_forks(count):
    forks = []
    for i in range(count):
        forks.append(
            {
                "name": f"fork-{i}",
                "full_name": f"bench-user/fork-{i}",
                "branch": "main",
                "parent": f"upstream-owner/repo-{i}",
                "parent_branch": "main",
                "parent_owner": "upstream-owner",
            }
        )
    return forks


def _measure_ms(task, runs):
    durations = []
    for _ in range(runs):
        start = time.perf_counter()
        task()
        durations.append((time.perf_counter() - start) * 1000.0)

    durations_sorted = sorted(durations)
    p95_idx = max(0, int(round(len(durations_sorted) * 0.95)) - 1)
    return {
        "min": round(min(durations_sorted), 3),
        "mean": round(statistics.mean(durations_sorted), 3),
        "max": round(max(durations_sorted), 3),
        "p95": round(durations_sorted[p95_idx], 3),
    }


def _fake_list_gh_api(endpoint, *_args, **_kwargs):
    if "/contents/.github/workflows/sync-fork.yml" in endpoint:
        return {"sha": "wf-sha"}
    if "/compare/" in endpoint:
        return {"behind_by": 2}
    return {
        "error": f"unexpected benchmark endpoint: {endpoint}",
        "error_type": "benchmark",
        "error_code": "E_BENCH_UNEXPECTED_ENDPOINT",
        "retryable": False,
        "status_code": None,
    }


def _bench_collect_fork_statuses(count, runs):
    forks = _build_forks(count)
    with patch.object(LIST_FORKS, "gh_api", side_effect=_fake_list_gh_api):

        def task():
            rows = LIST_FORKS._collect_fork_statuses(forks)
            if len(rows) != count:
                raise RuntimeError(
                    f"collect_fork_statuses mismatch: expected={count}, got={len(rows)}"
                )

        return _measure_ms(task, runs)


def _bench_sync_all_report(count, runs):
    forks = _build_forks(count)
    parsed = {
        "dry_run": False,
        "force": False,
        "add_mode": False,
        "json_output": True,
        "specific_repos": [],
        "show_help": False,
        "error": None,
    }

    with (
        patch.object(SYNC_FORKS, "check_gh_cli", return_value=None),
        patch.object(
            SYNC_FORKS,
            "get_auth_context",
            return_value={
                "username": "bench-user",
                "auth": {"configured": True, "mode": "env_token", "source": "GH_TOKEN"},
            },
        ),
        patch.object(
            SYNC_FORKS,
            "get_fork_repos_with_evidence",
            return_value=(
                forks,
                {
                    "source": "live_github_api",
                    "endpoint": "user/repos?affiliation=owner&visibility=all",
                    "fetched_at": "2026-04-25T10:00:00Z",
                    "authenticated_user": "bench-user",
                    "repo_count": count,
                    "fork_count": count,
                },
            ),
        ),
        patch.object(SYNC_FORKS, "_sync_now_result", return_value=SYNC_FORKS.RESULT_OK),
    ):

        def task():
            exit_code, report = SYNC_FORKS._run(parsed)
            if exit_code != SYNC_FORKS.EXIT_OK:
                raise RuntimeError(f"unexpected exit code: {exit_code}")
            if report["summary"]["matched"] != count:
                raise RuntimeError(
                    f"sync_all_report mismatch: expected={count}, got={report['summary']['matched']}"
                )

        return _measure_ms(task, runs)


def _bench_sync_specific_deduped(count, runs):
    forks = _build_forks(count)
    repeated_names = [fork["name"] for fork in forks] * 2
    parsed = {
        "dry_run": False,
        "force": False,
        "add_mode": False,
        "json_output": True,
        "specific_repos": repeated_names,
        "show_help": False,
        "error": None,
    }

    def fake_resolve(_username, repo_names):
        if len(repo_names) != count:
            raise RuntimeError(
                f"dedupe not applied: expected={count}, got={len(repo_names)}"
            )
        return forks, False

    with (
        patch.object(SYNC_FORKS, "check_gh_cli", return_value=None),
        patch.object(
            SYNC_FORKS,
            "get_auth_context",
            return_value={
                "username": "bench-user",
                "auth": {"configured": True, "mode": "env_token", "source": "GH_TOKEN"},
            },
        ),
        patch.object(SYNC_FORKS, "_resolve_specific_forks", side_effect=fake_resolve),
        patch.object(SYNC_FORKS, "_sync_now_result", return_value=SYNC_FORKS.RESULT_OK),
    ):

        def task():
            exit_code, report = SYNC_FORKS._run(parsed)
            if exit_code != SYNC_FORKS.EXIT_OK:
                raise RuntimeError(f"unexpected exit code: {exit_code}")
            if report["summary"]["matched"] != count:
                raise RuntimeError(
                    "sync_specific_deduped mismatch: "
                    f"expected={count}, got={report['summary']['matched']}"
                )

        return _measure_ms(task, runs)


def _run_benchmarks(sizes, runs):
    results = []
    for size in sizes:
        results.append(
            {
                "size": size,
                "collect_fork_statuses": _bench_collect_fork_statuses(size, runs),
                "sync_all_report": _bench_sync_all_report(size, runs),
                "sync_specific_deduped": _bench_sync_specific_deduped(size, runs),
            }
        )
    return results


def _check_thresholds(results):
    violations = []
    for row in results:
        size = row["size"]
        for metric_name, metric_values in row.items():
            if metric_name == "size":
                continue
            limit = MAX_MEAN_MS.get(metric_name, {}).get(size)
            if limit is None:
                continue
            mean_ms = metric_values["mean"]
            if mean_ms > limit:
                violations.append(
                    {
                        "metric": metric_name,
                        "size": size,
                        "mean_ms": mean_ms,
                        "limit_ms": limit,
                    }
                )
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run synthetic toolkit hot-path benchmarks.")
    parser.add_argument(
        "--sizes",
        default="10,100,500",
        help="Comma-separated dataset sizes (default: 10,100,500).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Benchmark runs per size (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail with exit code 1 if benchmark mean exceeds configured threshold.",
    )
    parser.add_argument(
        "--output",
        help="Optional output JSON path.",
    )
    args = parser.parse_args(argv)

    if args.runs <= 0:
        raise ValueError("--runs must be > 0")

    sizes = _parse_sizes(args.sizes)
    results = _run_benchmarks(sizes, args.runs)
    payload = {
        "tool": "benchmark_hotpaths",
        "root_dir": ROOT_DIR,
        "runs": args.runs,
        "sizes": list(sizes),
        "metrics": results,
    }

    violations = _check_thresholds(results) if args.check else []
    if args.check:
        payload["threshold_check"] = {
            "enabled": True,
            "violations": violations,
            "max_mean_ms": MAX_MEAN_MS,
        }

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output:
        output_path = os.path.abspath(args.output)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rendered)
            f.write("\n")

    if violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
