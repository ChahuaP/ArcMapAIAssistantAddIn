"""ArcMap Harness boundary MCP server entry point.

Run as ``python server/main.py`` (dsh spawns it over stdio). Starts the Py2
callback surface on 127.0.0.1:8765, then serves the MCP tools. The agent's
only door into ArcMap is this process.

Tool surface (B structure): every catalog operation is a first-class tool
generated from the operation cards; plus four infrastructure tools. There is
no generic run_operation dispatcher and no capability lookup tools — the tool
list is the capability list.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastmcp import FastMCP  # noqa: E402

from server import codegen  # noqa: E402
from server.callbacks import start_callback_server  # noqa: E402
from server.catalog import Catalog  # noqa: E402
from server.journal import OpJournal  # noqa: E402
from server.precheck import check as precheck
from server.precheck import coerce_arguments, resolve_layer_references  # noqa: E402
from server.session import BridgeUnavailable, BridgeSession  # noqa: E402

mcp = FastMCP("arcmap")

_catalog = Catalog()
_journal = OpJournal()
_session = BridgeSession()


def _unavailable(exc: BridgeUnavailable) -> dict:
    return {"status": "bridge_unavailable", "message": str(exc)}


# -- shared operation pipeline (one door, all tools) --------------------------

def _execute_operation(operation_id: str, arguments: Dict[str, Any]) -> dict:
    card = _catalog.get(operation_id)
    schema = card.get("parameters_schema", {})
    arguments = coerce_arguments(schema, arguments or {})
    try:
        context = _session.context_view() if _session_has_context() else None
    except BridgeUnavailable:
        context = None
    arguments = resolve_layer_references(schema, arguments, context)
    verdict = precheck(card, arguments, context)
    op_id = _journal.start_op(tool="operation", operation=operation_id,
                              arguments=arguments, precheck=verdict)
    _journal.event(op_id, "precheck", verdict)
    if verdict["status"] != "proven":
        _journal.finish_op(op_id, verdict["status"], verdict)
        return dict(verdict, op_id=op_id)
    try:
        outcome = _session.execute(op_id, operation_id, arguments, card)
        _journal.bind_run(op_id, outcome.get("run_id", op_id),
                          _session.context_digest(),
                          outcome.get("lease_id", ""),
                          int(outcome.get("epoch") or 0),
                          outcome.get("plan_hash", ""))
        _journal.finish_op(op_id, outcome.get("status", ""), outcome)
        _journal.event(op_id, "execution", outcome)
        return dict(outcome, op_id=op_id)
    except BridgeUnavailable as exc:
        _journal.finish_op(op_id, "bridge_unavailable", error=str(exc))
        return _unavailable(exc)
    except Exception as exc:  # noqa: BLE001 - boundary must return, not crash
        _journal.finish_op(op_id, "failed", error="%s: %s" % (type(exc).__name__, exc))
        _journal.event(op_id, "execution_error",
                       {"error": str(exc), "traceback": traceback.format_exc()[:2000]})
        return {"status": "failed", "op_id": op_id,
                "message": "%s: %s" % (type(exc).__name__, exc)}


def _session_has_context() -> bool:
    try:
        _session.targets()
        return True
    except BridgeUnavailable:
        return False


# -- generated operation tools (one per catalog card) --------------------------

def _make_operation_runner(operation_id: str, model):
    """Build the tool function for one operation.

    The generated pydantic model must be injected as a real object into
    ``__annotations__``: string annotations (PEP 563) would be evaluated
    against module globals where the closure-local model is invisible.
    """
    if model is None:
        def run() -> dict:
            return _execute_operation(operation_id, {})
    else:
        def run(arguments) -> dict:  # type: ignore[no-untyped-def]
            return _execute_operation(
                operation_id, arguments.model_dump(exclude_none=True))
        run.__annotations__["arguments"] = model
    return run


def _register_operation_tools() -> None:
    for operation_id in _catalog.operation_ids():
        card = _catalog.get(operation_id)
        model, _required = codegen.build_arguments_model(operation_id, card)
        description = codegen.tool_description(
            card, _catalog.side_effect_level(card))
        name = codegen.tool_name(operation_id)
        run = _make_operation_runner(operation_id, model)
        run.__name__ = name
        run.__qualname__ = name
        run.__doc__ = description
        mcp.tool(run)


_register_operation_tools()


# -- infrastructure tools -------------------------------------------------------

@mcp.tool
def get_map_context() -> dict:
    """获取当前 ArcMap 地图的实时上下文：图层、字段、几何类型、选择数、坐标系。

    每次调用都会重新捕获实时地图（不是缓存），执行任何写操作后再次调用
    能看到最新图层状态。如果返回 bridge_unavailable，说明 ArcMap 或 Bridge
    未运行，需要用户先打开 ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。
    """
    try:
        return {"status": "ok", **_session.context_view(force=True)}
    except BridgeUnavailable as exc:
        return _unavailable(exc)


@mcp.tool
def get_boundary_status() -> dict:
    """查询 ArcMap Harness 边界的实时连接状态：Bridge 进程是否存活、
    ArcMap 地图目标有几个（PID）、当前上下文缓存状态。

    回答"现在能不能操作 ArcMap"一律以本工具为准；它区分三种情况：
    边界在线、Bridge 进程在线（ArcMap 可能已关闭）、ArcMap 就绪（可执行）。
    """
    return _session.status()


@mcp.tool
def verify_result(op_id: str) -> dict:
    """对一个已执行的操作触发独立 ArcPy 验收探针（只需 op_id 一个参数，
    op_id 来自操作工具的返回值）。检查几何/字段/空间关系是否满足合同。"""
    try:
        record = _journal.get_op(op_id)
    except Exception:  # noqa: BLE001 - journal miss is a normal outcome
        record = None
    if record is None:
        return {"status": "unknown_op", "message": "op_id 不存在：%s" % op_id}
    try:
        card = _catalog.get(record.get("operation") or "")
        document = _session.verify(op_id, card)
        _journal.event(op_id, "verify", document)
        return {"status": "ok", "op_id": op_id, "probe": document}
    except BridgeUnavailable as exc:
        return _unavailable(exc)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": "%s: %s" % (type(exc).__name__, exc)}


@mcp.tool
def get_operation_history(limit: int = 10) -> dict:
    """查询最近的操作历史（op 级事实日志）：每次工具调用的 pre-check、执行与验收记录。"""
    return {"status": "ok", "ops": _journal.recent(max(1, min(int(limit), 100)))}


def main() -> None:
    start_callback_server(_session)
    mcp.run()


if __name__ == "__main__":
    main()
