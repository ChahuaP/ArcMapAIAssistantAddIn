from __future__ import annotations

import json

from gateway_py3 import arcmap_bridge_client
from gateway_py3.agent_engine.events import AGENT_PROGRESS_EVENT
from gateway_py3.llm_providers import FULL_AGENT_MODE, public_config
from gateway_py3.routes import arcmap


def plan_request(state, payload, port_checker=None):
    mode = payload.get("mode") or public_config()["default_mode"]
    request_id = str(payload.get("request_id") or "")
    context = payload.get("context")
    if mode == FULL_AGENT_MODE:
        publish_progress(state, "sync_arcmap", "同步 ArcMap", mode, request_id=request_id)
        context = arcmap.sync_context(state, port_checker=port_checker)["context"]
    elif context is None:
        publish_progress(state, "sync_arcmap", "同步 ArcMap", mode, request_id=request_id)
        context = arcmap.sync_context(state, port_checker=port_checker)["context"]

    row = state.planner.plan(payload["command"], context, mode=mode, request_id=request_id)
    state.reload_catalog()
    response = {"workflow": row}
    if mode == FULL_AGENT_MODE and (row.get("workflow") or {}).get("action") == "execute":
        publish_progress(state, "execute_arcmap", "执行到 ArcMap", mode, request_id)
        state.store.approve(row["id"])
        bridge = arcmap.active_bridge(state, port_checker=port_checker)
        response["execution"] = arcmap_bridge_client.execute_approved(
            allow_edits=True,
            port=bridge["port"],
            hwnd=bridge.get("hwnd")
        )
        response["workflow"] = state.store.get(row["id"])
        publish_progress(state, "complete", "完成", mode, request_id)
    return response


def publish_progress(state, stage, label, mode, request_id=""):
    events = getattr(state, "events", None)
    if events is None:
        return
    events.publish(AGENT_PROGRESS_EVENT, {
        "stage": stage,
        "label": label,
        "detail": "",
        "mode": mode,
        "request_id": request_id or "",
    })


def repair_custom_tool_workflow(state, workflow_id, payload):
    source = state.store.get(workflow_id)
    if source.get("status") != "failed":
        raise ValueError("只有执行失败的任务可以一键迭代自建工具。")
    operation_ids = custom_operation_ids(source.get("workflow") or {})
    if not operation_ids:
        raise ValueError("这个失败任务没有使用自建工具，不能进入自建工具迭代。")
    context = payload.get("context")
    if context is None:
        context = arcmap.sync_context(state)["context"]
    if not isinstance(context, dict):
        raise ValueError("context must be an object.")
    mode = source.get("mode") or public_config()["default_mode"]
    command = custom_tool_repair_command(source, operation_ids, payload.get("feedback") or "")
    row = state.planner.plan(command, context, mode=mode)
    state.reload_catalog()
    return {"workflow": row}


def custom_operation_ids(workflow):
    result = []
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation_id = step.get("operation")
        if isinstance(operation_id, str) and operation_id.startswith("custom.") and operation_id not in result:
            result.append(operation_id)
    return result


def custom_tool_repair_command(source, operation_ids, feedback):
    result = source.get("result") or {}
    error = result.get("error") if isinstance(result, dict) else ""
    traceback_text = result.get("traceback") if isinstance(result, dict) else ""
    extra = ""
    if isinstance(error, str) and "000840" in error and "空间参考" in error:
        extra = (
            "\n这个错误通常表示 CreateFeatureclass_management 的 spatial_reference 参数不是 ArcPy SpatialReference。"
            "修复时必须从输入图层读取：spatial_reference = arcpy.Describe(input_layer).spatialReference，"
            "不要传 context['spatial_reference']、spatialReference.name、factoryCode、字符串或 layer.spatialReference。"
        )
    if isinstance(feedback, str) and feedback.strip():
        feedback_text = "\n用户补充意见：%s" % feedback.strip()
    else:
        feedback_text = ""
    return (
        "进入自定义工具开发修复流程。上一次执行自建工具失败，请根据失败结果修订原工具。"
        "必须先调用 toolbuilder_get_draft 读取原工具，再调用 toolbuilder_revise_draft 修订同一个 tool_id；"
        "不要创建新工具，不要要求用户提供 executor 代码。"
        "\n涉及的自建 operation_id：%s"
        "\n原始用户请求：%s"
        "\n失败错误：%s"
        "%s"
        "%s"
        "\n失败工作流：%s"
        "\n失败 traceback：%s"
    ) % (
        "、".join(operation_ids),
        source.get("command") or "",
        error or "",
        extra,
        feedback_text,
        json.dumps(source.get("workflow") or {}, ensure_ascii=False, sort_keys=True),
        traceback_text or "",
    )
