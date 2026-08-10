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
    parser = argparse.ArgumentParser(description="Inspect the local GeoPilot ArcMap gateway.")
    parser.add_argument("--base-url", default=BASE_URL)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")
    subparsers.add_parser("diagnostics")
    subparsers.add_parser("capabilities")
    subparsers.add_parser("open-console")
    subparsers.add_parser("arcmap-list")

    args = parser.parse_args()
    try:
        if args.command == "health":
            return _print(_get(args.base_url, "/health"))
        if args.command == "diagnostics":
            return _print(_get(args.base_url, "/api/diagnostics"))
        if args.command == "capabilities":
            return _print(_get(args.base_url, "/api/capabilities"))
        if args.command == "open-console":
            webbrowser.open(args.base_url + "/")
            return 0
        if args.command == "arcmap-list":
            return _print(_get(args.base_url, "/arcmap/bridges"))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


def _get(base_url: str, path: str):
    return _request(base_url, path)


def _request(base_url: str, path: str):
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
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
