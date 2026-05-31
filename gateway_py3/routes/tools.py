from __future__ import annotations

from gateway_py3.tool_builder import delete_tool, enable_tool, reject_tool


def enable_pending_tool(state, tool_id):
    tool = enable_tool(state.store, tool_id)
    state.reload_catalog()
    return {"tool": tool, "operation_count": len(state.catalog.operations)}


def reject_pending_tool(state, tool_id):
    return {"tool": reject_tool(state.store, tool_id)}


def delete_pending_tool(state, tool_id):
    tool = delete_tool(state.store, tool_id)
    state.reload_catalog()
    return {"tool": tool, "operation_count": len(state.catalog.operations)}
