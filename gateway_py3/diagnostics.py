from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .llm_providers import public_config
from .paths import CATALOG_ROOT, appdata_dir, config_path, log_dir


def collect_diagnostics(app_version: str, operation_count: int, network_check: bool = True) -> Dict[str, Any]:
    config = public_config()
    install = _read_install_config()
    install_dir = Path(install.get("install_dir") or "") if install.get("install_dir") else None
    installed_version = install.get("app_version") or ""
    checks = [
        _check_gateway(app_version, operation_count),
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
        checks.extend(_check_provider_network(config))
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


def collect_agent_diagnostics(app_version: str, operation_count: int, state: Any) -> Dict[str, Any]:
    context_record = state.store.get_state("arcmap_context")
    context = context_record.get("value") if isinstance(context_record, dict) else None
    permission_record = state.store.get_state("arcmap_permission")
    permission = permission_record.get("value") if isinstance(permission_record, dict) else {}
    active_bridge_record = state.store.get_state("arcmap_active_bridge")
    active_bridge = active_bridge_record.get("value") if isinstance(active_bridge_record, dict) else {}
    categories = _catalog_categories(state)
    checks = [
        _check_gateway(app_version, operation_count),
        _item("experiment_runner", "实验运行", "ok", "GeoPilot 通过统一 run 接口生成可复现实验工作流。"),
        _check_agent_capabilities(operation_count, categories),
        _check_agent_context(context),
        _check_agent_bridge(active_bridge),
        _check_agent_permission(permission),
    ]
    return {
        "ok": all(item["status"] == "ok" for item in checks),
        "app_version": app_version,
        "operation_count": operation_count,
        "categories": categories,
        "context": _agent_context_summary(context),
        "active_bridge": active_bridge if isinstance(active_bridge, dict) else {},
        "permission": permission if isinstance(permission, dict) else {},
        "checks": checks,
        "first_run_steps": [
            "health",
            "arcmap-list",
            "多开 ArcMap 时只保留一个窗口，或用外部 agent 指定 hwnd",
            "arcmap-sync",
            "capabilities",
            "run --mode context_single --command <request>",
            "run",
            "run-status"
        ]
    }


def _read_install_config() -> Dict[str, Any]:
    path = _install_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"error": str(exc)}


def _install_config_path() -> Path:
    return appdata_dir() / "install.json"


def _check_gateway(app_version: str, operation_count: int) -> Dict[str, Any]:
    return _item(
        "gateway",
        "本地网关",
        "ok",
        "已启动，版本 %s，能力 %s 个。" % (app_version, operation_count),
    )


def _check_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(config.get("config_path") or str(config_path()))
    providers = config.get("providers") or {}
    required = sorted(set([config.get("primary_provider"), config.get("reviewer_provider")]))
    missing = [provider for provider in required if provider and not (providers.get(provider) or {}).get("has_api_key")]
    if not missing:
        labels = "、".join((providers.get(provider) or {}).get("label", provider) for provider in required)
        return _item("provider_key", "模型 Key", "ok", "已读取 %s Key。" % labels, path)
    if path.exists():
        return _item("provider_key", "模型 Key", "bad", "配置文件存在，但缺少模型 Key：%s。" % "、".join(missing), path)
    return _item("provider_key", "模型 Key", "bad", "还没有保存模型 API Key。", path)


def _check_gateway_catalog() -> Dict[str, Any]:
    path = CATALOG_ROOT / "catalog.json"
    if path.exists():
        return _item("gateway_catalog", "网关操作目录", "ok", "网关可读取操作目录。", path)
    return _item("gateway_catalog", "网关操作目录", "bad", "网关缺少 operation_catalog。", path)


def _catalog_categories(state: Any) -> Dict[str, int]:
    categories: Dict[str, int] = {}
    for operation in state.catalog.all_operations():
        category = str(operation.get("category") or "unknown")
        categories[category] = categories.get(category, 0) + 1
    return categories


def _check_agent_capabilities(operation_count: int, categories: Dict[str, int]) -> Dict[str, Any]:
    if operation_count <= 0:
        return _item("agent_capabilities", "Agent 能力目录", "bad", "没有可用 operation。")
    detail = "已加载 %s 个 operation，分类：%s。" % (
        operation_count,
        "、".join("%s=%s" % (key, categories[key]) for key in sorted(categories))
    )
    return _item("agent_capabilities", "Agent 能力目录", "ok", detail)


def _check_agent_context(context: Any) -> Dict[str, Any]:
    if isinstance(context, dict) and context:
        layer_count = len(context.get("layers") or [])
        return _item("agent_context", "ArcMap 上下文", "ok", "已同步，上下文含 %s 个图层。" % layer_count)
    return _item("agent_context", "ArcMap 上下文", "warn", "还没有同步上下文；先运行 arcmap-list，再运行 arcmap-sync。")


def _check_agent_bridge(active_bridge: Any) -> Dict[str, Any]:
    if isinstance(active_bridge, dict) and active_bridge.get("port"):
        title = ((active_bridge.get("summary") or {}).get("title") or "").strip()
        detail = "已连接 ArcMap Bridge，port=%s。" % active_bridge.get("port")
        if title:
            detail = detail[:-1] + "，当前窗口=%s。" % title
        return _item("agent_bridge", "地图窗口", "ok", detail)
    return _item("agent_bridge", "地图窗口", "warn", "未固定 ArcMap 窗口；多开时请只保留一个窗口，或用外部 agent 指定 hwnd。")


def _check_agent_permission(permission: Any) -> Dict[str, Any]:
    if not isinstance(permission, dict):
        permission = {}
    if permission.get("auto_execute") and permission.get("allow_edits"):
        return _item("agent_permission", "自动执行权限", "ok", "已允许全自动执行，且允许直接数据编辑。")
    if permission.get("auto_execute"):
        return _item("agent_permission", "自动执行权限", "ok", "已允许全自动执行；直接编辑数据仍会被拦截，除非 allow_edits=true。")
    return _item("agent_permission", "自动执行权限", "warn", "未开启全自动执行；执行前需要用户确认，或运行 arcmap-permission --auto-execute。")


def _agent_context_summary(context: Any) -> Dict[str, Any]:
    if not isinstance(context, dict) or not context:
        return {"synced": False, "layer_count": 0}
    return {
        "synced": True,
        "layer_count": len(context.get("layers") or []),
        "mxd_path": context.get("mxd_path") or "",
        "is_saved": bool(context.get("is_saved")),
    }


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
    addin_dirs = install.get("addin_dirs") or install.get("addin_dir") or ""
    if isinstance(addin_dirs, str):
        addin_dirs = [addin_dirs] if addin_dirs else []
    if not addin_dirs:
        return _item("installed_addin", "ArcMap 插件", "warn", "安装记录里没有插件目录。请重新安装最新版。")
    paths = [Path(addin_dir) / "arcmapaiassistantaddin.esriaddin" for addin_dir in addin_dirs]
    missing = [path for path in paths if not path.exists()]
    if not missing:
        return _item("installed_addin", "ArcMap 插件", "ok", "插件文件存在：%s 个目录。" % len(paths), paths[0])
    return _item("installed_addin", "ArcMap 插件", "bad", "插件文件不存在：%s" % ", ".join([str(path) for path in missing]), missing[0])


def _check_provider_network(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = []
    providers = config.get("providers") or {}
    required = sorted(set([config.get("primary_provider"), config.get("reviewer_provider")]))
    for provider_id in required:
        provider = providers.get(provider_id) or {}
        if not provider.get("has_api_key"):
            continue
        checks.extend(_check_one_provider_network(provider_id, provider))
    return checks


def _check_one_provider_network(provider_id: str, provider: Dict[str, Any]) -> List[Dict[str, Any]]:
    label = provider.get("label") or provider_id
    base_url = provider.get("base_url") or ""
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return [_item("%s_url" % provider_id, "%s 地址" % label, "bad", "%s Base URL 不正确：%s。" % (label, base_url))]
    checks = [_item("%s_url" % provider_id, "%s 地址" % label, "ok", base_url)]
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        checks.append(_item("%s_dns" % provider_id, "%s DNS" % label, "ok", "域名可解析：%s。" % host))
    except (OSError, socket.gaierror) as exc:
        checks.append(_item("%s_dns" % provider_id, "%s DNS" % label, "bad", "域名解析失败：%s。" % exc))
        checks.append(_item("%s_tcp" % provider_id, "%s 连接" % label, "bad", "DNS 失败，未尝试连接。"))
        return checks
    try:
        address = addresses[0][4]
        with socket.create_connection(address, timeout=3):
            pass
        checks.append(_item("%s_tcp" % provider_id, "%s 连接" % label, "ok", "可以连接 %s:%s。" % (host, port)))
    except OSError as exc:
        checks.append(_item("%s_tcp" % provider_id, "%s 连接" % label, "bad", "连接失败：%s。" % exc))
    return checks


def _check_log_dir() -> Dict[str, Any]:
    path = log_dir()
    return _item("logs", "日志目录", "ok", "运行日志会写入这里。", path)


def _read_text(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {"value": "", "error": ""}
    try:
        return {"value": path.read_text(encoding="utf-8-sig").strip(), "error": ""}
    except (OSError, UnicodeDecodeError) as exc:
        return {"value": "", "error": str(exc)}


def _item(item_id: str, label: str, status: str, detail: str, path: Optional[Path] = None) -> Dict[str, Any]:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "detail": detail,
        "path": str(path) if path else "",
    }
