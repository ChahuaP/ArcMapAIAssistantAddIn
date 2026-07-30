#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import webbrowser


BASE_URL = "http://127.0.0.1:8765"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible GeoPilot ArcMap experiments and controlled execution.")
    parser.add_argument("--base-url", default=BASE_URL)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("doctor")
    subparsers.add_parser("agent-diagnostics")
    capabilities_parser = subparsers.add_parser("capabilities")
    capabilities_parser.add_argument("--detail", action="store_true", help="Include full operation schemas.")
    subparsers.add_parser("runs")
    subparsers.add_parser("open-console")
    subparsers.add_parser("arcmap-health")
    subparsers.add_parser("arcmap-list")

    select_parser = subparsers.add_parser("arcmap-select")
    select_parser.add_argument("--pid", type=int, default=0)
    select_parser.add_argument("--port", type=int, default=0)
    select_parser.add_argument("--hwnd", type=int, default=0)

    permission_parser = subparsers.add_parser("arcmap-permission")
    permission_parser.add_argument("--auto-execute", action="store_true")
    permission_parser.add_argument("--allow-edits", action="store_true")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--mode", required=True, choices=("direct_single", "context_single", "constrained_single", "multi_agent"))
    run_parser.add_argument("--command", dest="user_command", required=True)
    run_parser.add_argument("--model", default="")
    run_parser.add_argument("--provider", default="")
    run_parser.add_argument("--execute", action="store_true")
    run_parser.add_argument("--confirmed", action="store_true")
    run_parser.add_argument("--allow-edits", action="store_true")
    run_status_parser = subparsers.add_parser("run-status")
    run_status_parser.add_argument("run_id")
    cancel_parser = subparsers.add_parser("run-cancel")
    cancel_parser.add_argument("run_id")
    report_parser = subparsers.add_parser("run-report")
    report_parser.add_argument("--mode", choices=("direct_single", "context_single", "constrained_single", "multi_agent"))

    args = parser.parse_args()
    try:
        if args.command == "health":
            return _print(_get(args.base_url, "/health"))
        if args.command == "doctor":
            return _print(_get(args.base_url, "/agent/diagnostics"))
        if args.command == "agent-diagnostics":
            return _print(_get(args.base_url, "/agent/diagnostics"))
        if args.command == "capabilities":
            path = "/api/capabilities?detail=1" if args.detail else "/api/capabilities"
            return _print(_get(args.base_url, path))
        if args.command == "runs":
            return _print(_get(args.base_url, "/api/runs"))
        if args.command == "open-console":
            webbrowser.open(args.base_url + "/")
            return 0
        if args.command == "arcmap-health":
            return _print(_get(args.base_url, "/arcmap/health"))
        if args.command == "arcmap-list":
            return _print(_get(args.base_url, "/arcmap/bridges"))
        if args.command == "arcmap-select":
            return _print(_post(args.base_url, "/arcmap/active", {
                "pid": args.pid,
                "port": args.port,
                "hwnd": args.hwnd
            }))
        if args.command == "arcmap-permission":
            return _print(_post(args.base_url, "/arcmap/permission", {
                "auto_execute": args.auto_execute,
                "allow_edits": args.allow_edits
            }))
        if args.command == "run":
            payload = {
                "mode": args.mode,
                "command": args.user_command,
                "execute": args.execute,
                "confirmed": args.confirmed,
                "allow_edits": args.allow_edits,
            }
            if args.provider:
                payload["provider"] = args.provider
            if args.model:
                payload["model"] = args.model
            return _print(_post(args.base_url, "/runs", payload))
        if args.command == "run-status":
            return _print(_get(args.base_url, "/runs/%s" % args.run_id))
        if args.command == "run-cancel":
            return _print(_post(args.base_url, "/runs/%s/cancel" % args.run_id, {}))
        if args.command == "run-report":
            suffix = "?mode=%s" % args.mode if args.mode else ""
            return _print(_get(args.base_url, "/runs/report" + suffix))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


def _get(base_url: str, path: str):
    return _request(base_url, path, None)


def _post(base_url: str, path: str, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(base_url, path, data)


def _request(base_url: str, path: str, data):
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
            message = payload.get("error") or body
        except ValueError:
            message = body
        raise RuntimeError("GeoPilot request failed: %s" % message)
    except urllib.error.URLError as exc:
        raise RuntimeError("GeoPilot gateway is not reachable at %s: %s" % (base_url, exc.reason))


def _print(payload) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
