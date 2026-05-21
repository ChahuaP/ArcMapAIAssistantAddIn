from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .deepseek_client import public_config
from .paths import CATALOG_ROOT, appdata_dir, config_path, log_dir


def collect_diagnostics(app_version: str, catalog_version: str, operation_count: int, network_check: bool = True) -> Dict[str, Any]:
    config = public_config()
    install = _read_install_config()
    install_dir = Path(install.get("install_dir") or "") if install.get("install_dir") else None
    installed_version = install.get("app_version") or ""
    checks = [
        _check_gateway(app_version, catalog_version, operation_count),
        _check_config(config),
        _check_gateway_catalog(),
        _check_install_config(install),
    ]
    if install_dir:
        checks.extend(_check_install_dir(install_dir, app_version, installed_version))
    else:
        checks.append(_item("install_dir", "安装目录", "warn", "没有找到安装目录记录。开发模式可忽略。"))
    checks.append(_check_addin(install))
    if network_check:
        checks.extend(_check_deepseek_network(config))
    checks.append(_check_log_dir())
    return {
        "ok": all(item["status"] == "ok" for item in checks),
        "app_version": app_version,
        "install": {
            "install_dir": str(install_dir) if install_dir else "",
            "app_version": installed_version,
            "config_path": str(_install_config_path()),
        },
        "checks": checks,
    }


def _read_install_config() -> Dict[str, Any]:
    path = _install_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"error": str(exc)}


def _install_config_path() -> Path:
    return appdata_dir() / "install.json"


def _check_gateway(app_version: str, catalog_version: str, operation_count: int) -> Dict[str, Any]:
    return _item(
        "gateway",
        "本地网关",
        "ok",
        "已启动，版本 %s，能力 %s 个，目录版本 %s。" % (app_version, operation_count, catalog_version),
    )


def _check_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(config.get("config_path") or str(config_path()))
    if config.get("has_deepseek_api_key"):
        return _item("deepseek_key", "DeepSeek Key", "ok", "已读取 Key。", path)
    if path.exists():
        return _item("deepseek_key", "DeepSeek Key", "bad", "配置文件存在，但没有 deepseek_api_key 字段。", path)
    return _item("deepseek_key", "DeepSeek Key", "bad", "还没有保存 DeepSeek API Key。", path)


def _check_gateway_catalog() -> Dict[str, Any]:
    path = CATALOG_ROOT / "catalog.json"
    if path.exists():
        return _item("gateway_catalog", "网关操作目录", "ok", "网关可读取操作目录。", path)
    return _item("gateway_catalog", "网关操作目录", "bad", "网关缺少 operation_catalog。", path)


def _check_install_config(install: Dict[str, Any]) -> Dict[str, Any]:
    path = _install_config_path()
    if install.get("error"):
        return _item("install_config", "安装记录", "bad", "install.json 读取失败：%s" % install["error"], path)
    if install.get("install_dir"):
        return _item("install_config", "安装记录", "ok", "已记录安装目录。", path)
    return _item("install_config", "安装记录", "warn", "没有 install.json，可能正在开发模式运行。", path)


def _check_install_dir(install_dir: Path, app_version: str, installed_version: str) -> List[Dict[str, Any]]:
    required = [
        ("installed_runtime", "ArcMap runtime", install_dir / "arcmap_runtime_py2" / "runtime.py"),
        ("installed_catalog", "执行操作目录", install_dir / "operation_catalog" / "catalog.json"),
        ("installed_gateway", "网关 EXE", install_dir / "gateway" / "ArcMapAIAssistantGateway.exe"),
        ("installed_open_cmd", "控制台脚本", install_dir / "OpenAssistantWeb.cmd"),
        ("installed_start_cmd", "后台脚本", install_dir / "StartGateway.cmd"),
    ]
    checks = []
    if install_dir.exists():
        checks.append(_item("install_dir", "安装目录", "ok", "安装目录存在。", install_dir))
    else:
        checks.append(_item("install_dir", "安装目录", "bad", "安装目录不存在。", install_dir))
    for item_id, label, path in required:
        if path.exists():
            checks.append(_item(item_id, label, "ok", "文件存在。", path))
        else:
            checks.append(_item(item_id, label, "bad", "缺少文件。", path))
    version_path = install_dir / "VERSION"
    file_version_result = _read_text(version_path)
    if file_version_result["error"]:
        checks.append(_item("installed_version", "安装版本", "bad", "VERSION 读取失败：%s" % file_version_result["error"], version_path))
        return checks
    file_version = file_version_result["value"]
    if file_version and installed_version and file_version != installed_version:
        checks.append(_item("installed_version", "安装版本", "warn", "VERSION 是 %s，install.json 是 %s。请重新安装最新版。" % (file_version, installed_version), version_path))
        return checks
    installed_version = installed_version or file_version
    if installed_version and installed_version == app_version:
        checks.append(_item("installed_version", "安装版本", "ok", "安装版本与网关一致：%s。" % app_version, version_path))
    elif installed_version:
        checks.append(_item("installed_version", "安装版本", "warn", "安装版本 %s，当前网关 %s。请重新安装最新版。" % (installed_version, app_version), version_path))
    else:
        checks.append(_item("installed_version", "安装版本", "warn", "没有安装版本记录。请重新安装最新版。", version_path))
    return checks


def _check_addin(install: Dict[str, Any]) -> Dict[str, Any]:
    addin_dir = install.get("addin_dir") or ""
    if not addin_dir:
        return _item("installed_addin", "ArcMap 插件", "warn", "安装记录里没有插件目录。请重新安装最新版。")
    path = Path(addin_dir) / "arcmapaiassistantaddin.esriaddin"
    if path.exists():
        return _item("installed_addin", "ArcMap 插件", "ok", "插件文件存在。", path)
    return _item("installed_addin", "ArcMap 插件", "bad", "插件文件不存在。", path)


def _check_deepseek_network(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_url = config.get("base_url") or "https://api.deepseek.com"
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return [_item("deepseek_url", "DeepSeek 地址", "bad", "DeepSeek Base URL 不正确：%s。" % base_url)]
    checks = [_item("deepseek_url", "DeepSeek 地址", "ok", base_url)]
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        checks.append(_item("deepseek_dns", "DeepSeek DNS", "ok", "域名可解析：%s。" % host))
    except Exception as exc:
        checks.append(_item("deepseek_dns", "DeepSeek DNS", "bad", "域名解析失败：%s。" % exc))
        checks.append(_item("deepseek_tcp", "DeepSeek 连接", "bad", "DNS 失败，未尝试连接。"))
        return checks
    try:
        address = addresses[0][4]
        with socket.create_connection(address, timeout=3):
            pass
        checks.append(_item("deepseek_tcp", "DeepSeek 连接", "ok", "可以连接 %s:%s。" % (host, port)))
    except Exception as exc:
        checks.append(_item("deepseek_tcp", "DeepSeek 连接", "bad", "连接失败：%s。" % exc))
    return checks


def _check_log_dir() -> Dict[str, Any]:
    path = log_dir()
    return _item("logs", "日志目录", "ok", "运行日志会写入这里。", path)


def _read_text(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {"value": "", "error": ""}
    try:
        return {"value": path.read_text(encoding="utf-8-sig").strip(), "error": ""}
    except Exception as exc:
        return {"value": "", "error": str(exc)}


def _item(item_id: str, label: str, status: str, detail: str, path: Optional[Path] = None) -> Dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "detail": detail,
        "path": str(path) if path else "",
    }
