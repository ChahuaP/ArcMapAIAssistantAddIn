"""Pydantic output models for model calls (§13.1).

These replace hand-written dict validation in WorkflowEngine. Each model is
passed as ``response_model`` to ``ModelRuntime.invoke`` so that
``model_validate`` runs before the result is cached (§6.4: only
schema-validated responses enter the cache).

The models match the **actual adapter response shape** after
``_extract_response`` (provider wire noise stripped):

* planner / repair → ``chat_with_tools`` → ``{"tool_calls": [...]}``
* audit → ``chat_structured`` → ``{"audit_result": {"decision":..., "claims":[...]}}``

Capability-closure and step-id synthesis stay in WorkflowEngine: these models
only assert the wire-level structure the provider committed to.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """Same strictness as kernel contracts: frozen, no extra fields, validate
    on assignment (§13.1)."""
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )


class ToolCallModel(_StrictModel):
    """One tool call in a planner/repair draft response."""
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_assignment=True,
    )

    name: str
    arguments: Dict[str, Any]


class PlannerDraftModel(_StrictModel):
    """Validated shape of a planning draft (``_generate_draft``)."""
    tool_calls: List[ToolCallModel] = Field(min_length=1)


class RepairDraftModel(_StrictModel):
    """Validated shape of a repair draft (``_request_repair``).

    Same wire shape as the planner draft: the model re-emits the full workflow
    with the requested fixes applied.
    """
    tool_calls: List[ToolCallModel] = Field(min_length=1)


_AUDIT_DECISIONS = Literal["pass", "revise", "clarify", "reject"]
_AUDIT_KINDS = Literal["revision", "clarification", "rejection"]
_CHANGE_TARGETS = Literal["workflow", "task_contract", "none"]


class AuditClaimModel(_StrictModel):
    """One proof-bound G3 audit claim."""
    kind: _AUDIT_KINDS
    proof_id: str = Field(min_length=1)
    change_target: _CHANGE_TARGETS
    required_change: str = Field(min_length=1)


class AuditResultBody(_StrictModel):
    """Inner body of the audit response (matches AuditContract.validate_shape)."""
    decision: _AUDIT_DECISIONS
    claims: List[AuditClaimModel]


class AuditResultModel(_StrictModel):
    """Validated shape of an audit response.

    The ``audit_result`` wrapper is part of the tool schema and survives
    ``_extract_response``; the consumer (``_request_audit``) unwraps
    ``response["audit_result"]`` to get the body.
    """
    audit_result: AuditResultBody
