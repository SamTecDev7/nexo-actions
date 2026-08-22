#!/usr/bin/env python3
"""Reproducible storage scanner benchmark for the real Nexo environment."""

import argparse
import json
import time

from nexo_actions.storage_tools import clear_storage_cache, find_large_folders


def run(scope, backend, timeout):
    clear_storage_cache()
    updates = []
    started = time.perf_counter()
    result = find_large_folders(
        scope,
        10,
        backend=backend,
        timeout_seconds=timeout,
        use_cache=False,
        progress_callback=updates.append,
    )
    return {
        "scope": scope,
        "backend": result.get("backend", backend),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "complete": result["complete"],
        "timed_out": result["timed_out"],
        "files_scanned": result["files_scanned"],
        "folders_scanned": result["folders_scanned"],
        "progress_events": len(updates),
        "top_folders": result["folders"][:3],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["downloads", "profile", "all"], default="all")
    parser.add_argument("--backend", choices=["windows", "python", "auto"], default="auto")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    scopes = ["downloads", "profile"] if args.scope == "all" else [args.scope]
    print(json.dumps([run(scope, args.backend, args.timeout) for scope in scopes], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
