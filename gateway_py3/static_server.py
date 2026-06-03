from __future__ import annotations

import mimetypes
from pathlib import Path

from gateway_py3.paths import WEB_ROOT


def is_static_path(path: str) -> bool:
    return path == "/" or path.startswith("/web/") or _direct_asset(path) is not None


def serve_static(handler, path: str) -> None:
    target = _static_target(path)
    if target is None:
        handler._json({"error": "Not found"}, 404)
        return
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _static_target(path: str) -> Path | None:
    rel = _relative_path(path)
    if rel is None:
        return None
    root = WEB_ROOT.resolve()
    target = (WEB_ROOT / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.exists() or not target.is_file():
        return None
    return target


def _relative_path(path: str) -> str | None:
    if path == "/":
        return "index.html"
    if path.startswith("/web/"):
        return path[len("/web/"):]
    return _direct_asset(path)


def _direct_asset(path: str) -> str | None:
    if "/" not in path[1:]:
        name = path.lstrip("/")
        if name in {
            "index.html",
            "tokens.css",
            "styles.css",
            "components.css",
            "app.js",
            "app_render.js",
            "app_mentions.js",
            "app_voice.js",
            "favicon.svg",
        }:
            return name
    return None
