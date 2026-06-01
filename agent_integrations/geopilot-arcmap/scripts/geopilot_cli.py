#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


BASE_URL = "http://127.0.0.1:8765"


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the local GeoPilot ArcMap gateway without invoking GeoPilot's planner.")
    parser.add_argument("--base-url", default=BASE_URL)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("doctor")
    subparsers.add_parser("agent-diagnostics")
    subparsers.add_parser("context")
    capabilities_parser = subparsers.add_parser("capabilities")
    capabilities_parser.add_argument("--detail", action="store_true", help="Include full operation schemas.")
    subparsers.add_parser("workflows")
    subparsers.add_parser("open-console")
    subparsers.add_parser("approve-latest")
    subparsers.add_parser("arcmap-health")
    subparsers.add_parser("arcmap-list")
    subparsers.add_parser("arcmap-sync")

    select_parser = subparsers.add_parser("arcmap-select")
    select_parser.add_argument("--pid", type=int, default=0)
    select_parser.add_argument("--port", type=int, default=0)
    select_parser.add_argument("--hwnd", type=int, default=0)

    permission_parser = subparsers.add_parser("arcmap-permission")
    permission_parser.add_argument("--auto-execute", action="store_true")
    permission_parser.add_argument("--allow-edits", action="store_true")

    execute_approved_parser = subparsers.add_parser("arcmap-execute-approved")
    execute_approved_parser.add_argument("--confirmed", action="store_true")
    execute_approved_parser.add_argument("--allow-edits", action="store_true")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--workflow", required=True, help="Workflow JSON file, or '-' for stdin.")
    validate_parser.add_argument("--context", help="Optional context JSON file, or '-' for stdin.")

    propose_parser = subparsers.add_parser("propose")
    propose_parser.add_argument("--workflow", required=True, help="Workflow JSON file, or '-' for stdin.")
    propose_parser.add_argument("--context", help="Optional context JSON file.")
    propose_parser.add_argument("--command", dest="user_command", default="")
    propose_parser.add_argument("--source", default="codex")
    propose_parser.add_argument("--project-id", default="")

    execute_workflow_parser = subparsers.add_parser("arcmap-execute-workflow")
    execute_workflow_parser.add_argument("--workflow", required=True, help="Workflow JSON file, or '-' for stdin.")
    execute_workflow_parser.add_argument("--context", help="Optional context JSON file.")
    execute_workflow_parser.add_argument("--command", dest="user_command", default="")
    execute_workflow_parser.add_argument("--source", default="codex")
    execute_workflow_parser.add_argument("--project-id", default="")
    execute_workflow_parser.add_argument("--confirmed", action="store_true")
    execute_workflow_parser.add_argument("--allow-edits", action="store_true")

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("workflow_id")

    args = parser.parse_args()
    try:
        if args.command == "health":
            return _print(_get(args.base_url, "/health"))
        if args.command == "doctor":
            return _print(_get(args.base_url, "/agent/diagnostics"))
        if args.command == "agent-diagnostics":
            return _print(_get(args.base_url, "/agent/diagnostics"))
        if args.command == "context":
            return _print(_get(args.base_url, "/context"))
        if args.command == "capabilities":
            path = "/api/capabilities?detail=1" if args.detail else "/api/capabilities"
            return _print(_get(args.base_url, path))
        if args.command == "workflows":
            return _print(_get(args.base_url, "/api/workflows"))
        if args.command == "open-console":
            webbrowser.open(args.base_url + "/")
            return 0
        if args.command == "approve-latest":
            workflows = _get(args.base_url, "/api/workflows").get("workflows") or []
            workflow_id = _latest_draft_workflow_id(workflows)
            if not workflow_id:
                raise RuntimeError("No draft workflow is available to approve.")
            return _print(_post(args.base_url, "/workflows/%s/approve" % workflow_id, {}))
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
        if args.command == "arcmap-sync":
            return _print(_post(args.base_url, "/arcmap/sync", {}))
        if args.command == "arcmap-permission":
            return _print(_post(args.base_url, "/arcmap/permission", {
                "auto_execute": args.auto_execute,
                "allow_edits": args.allow_edits
            }))
        if args.command == "arcmap-execute-approved":
            return _print(_post(args.base_url, "/arcmap/execute-approved", {
                "confirmed": args.confirmed,
                "allow_edits": args.allow_edits
            }))
        if args.command == "validate":
            payload = {"workflow": _read_json_arg(args.workflow)}
            if args.context:
                payload["context"] = _read_json_arg(args.context)
            return _print(_post(args.base_url, "/agent/workflows/validate", payload))
        if args.command == "propose":
            payload = {
                "workflow": _read_json_arg(args.workflow),
                "command": args.user_command,
                "source": args.source,
                "project_id": args.project_id
            }
            if getattr(args, "context", None):
                payload["context"] = _read_json_arg(args.context)
            return _print(_post(args.base_url, "/agent/workflows/propose", payload))
        if args.command == "arcmap-execute-workflow":
            payload = {
                "workflow": _read_json_arg(args.workflow),
                "command": args.user_command,
                "source": args.source,
                "project_id": args.project_id,
                "confirmed": args.confirmed,
                "allow_edits": args.allow_edits
            }
            if getattr(args, "context", None):
                payload["context"] = _read_json_arg(args.context)
            return _print(_post(args.base_url, "/arcmap/execute-workflow", payload))
        if args.command == "approve":
            return _print(_post(args.base_url, "/workflows/%s/approve" % args.workflow_id, {}))
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


def _read_json_arg(value: str):
    if value == "-":
        return json.load(sys.stdin)
    return json.loads(Path(value).read_text(encoding="utf-8-sig"))


def _latest_draft_workflow_id(workflows) -> str:
    for workflow in workflows:
        if workflow.get("status") == "draft":
            return str(workflow.get("id") or "")
    return ""


def _print(payload) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
