from __future__ import annotations

from gateway_py3.validators import context_hash, prepare_workflow


def validate_workflow(state, payload):
    context = context_from_payload(state, payload)
    workflow = payload.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be an object.")
    prepared = prepare_workflow(workflow, state.catalog, context)
    return {
        "ok": True,
        "context_hash": context_hash(context),
        "workflow": prepared
    }


def propose_workflow(state, payload):
    result = validate_workflow(state, payload)
    workflow = result["workflow"]
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        command = workflow.get("summary") or "External agent workflow"
    row = state.store.create_draft(
        command=command.strip(),
        context_hash=result["context_hash"],
        workflow=workflow,
        agent_trace=[{
            "type": "external_agent",
            "source": str(payload.get("source") or "external_agent")
        }],
        mode="external_agent"
    )
    return {"ok": True, "workflow": row}


def context_from_payload(state, payload):
    context = payload.get("context")
    if isinstance(context, dict):
        return context
    stored_context = state.store.get_state("arcmap_context")
    if not stored_context:
        raise ValueError("请先让 agent 运行 arcmap-list 和 arcmap-sync 读取 ArcMap 上下文。")
    return stored_context["value"]
