"""PolicyGate: the only authorizer (§6.6).

``authorize(actor, verified_plan, runtime_lease, requested_effects)`` binds
actor identity, run, plan hash, input/output identities, side-effect level,
lease and expiry into an ``AuthorizationGrant``. Routing choice is not
authorization; an Agent request is not authorization. The gate never grants
itself permission (§2.3).

Side-effect levels (§6.6):
  1 read-only query
  2 recoverable map-session change (selection / visibility / view)
  3 isolated-workspace data write
  4 destructive edit or overwrite publication (disabled by default)

Level 4 is disabled unless the underlying capability can prove transactional
rollback, explicit object identity and explicit authorization. Operations that
cannot guarantee atomicity must be denied, not "proceed after a warning".
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional, Tuple

from ..kernel import contracts
from ..kernel.contracts import (
    AuthorizationGrant, CallerIdentity, Outcome, RequestEnvelope,
    RuntimeLease, VerifiedPlan, outcome_succeeded, outcome_failed,
    POLICY_DENIED, CONTRACT_FAILED, INFRASTRUCTURE_FAILED,
)

GRANT_LIFETIME_SECONDS = 600.0
# Level 4 is disabled by default (§6.6); enable explicitly per deployment.
DESTRUCTIVE_ENABLED = False


class PolicyGate:
    """Deterministic authorization for one run (§6.6).

    ``authorize`` returns either a succeeded Outcome whose ``details["grant"]``
    is the bound ``AuthorizationGrant``, or a terminal ``PolicyDenied`` /
    ``ContractFailed`` Outcome. The kernel persists the grant via the store;
    the gate itself never writes state.
    """

    def __init__(self, grant_lifetime_seconds: float = GRANT_LIFETIME_SECONDS,
                 destructive_enabled: bool = DESTRUCTIVE_ENABLED):
        self.grant_lifetime_seconds = float(grant_lifetime_seconds)
        self.destructive_enabled = bool(destructive_enabled)

    def precheck(self, actor: RequestEnvelope, plan: VerifiedPlan,
                 requested_effects: Dict[str, Any]) -> Outcome:
        """Validate side-effect level + plan binding without a lease (§6.6).

        Runs before the user is asked to approve; the real grant is issued by
        ``authorize`` once the runtime lease exists. Never constructs a fake
        lease or persists anything.
        """
        effects = requested_effects if isinstance(requested_effects, dict) else {}
        level = int(effects.get("level", 1))
        if level < 1 or level > 4:
            return outcome_failed(
                CONTRACT_FAILED, "authorization", "invalid_effect_level",
                "副作用等级必须为 1..4。",
            )
        if level > plan.risk_level:
            return outcome_failed(
                POLICY_DENIED, "authorization", "effect_exceeds_plan",
                "请求的副作用等级超过计划声明的风险等级。",
            )
        if level == 4 and not self.destructive_enabled:
            return outcome_failed(
                POLICY_DENIED, "authorization", "destructive_disabled",
                "破坏性编辑/覆盖发布默认禁用。",
            )
        if level == 4 and not effects.get("transactional"):
            return outcome_failed(
                POLICY_DENIED, "authorization", "destructive_not_atomic",
                "破坏性操作必须提供可证明的事务回滚。",
            )
        return outcome_succeeded("authorization", "预检通过。")

    def authorize(self, actor: RequestEnvelope, plan: VerifiedPlan,
                  runtime_lease: RuntimeLease,
                  requested_effects: Dict[str, Any]) -> Outcome:
        """Authorize a sealed plan for one actor + lease.

        ``requested_effects`` carries the side-effect level the run asks for
        (normally ``intent.acceptable_side_effects``) plus optional named
        input/output identities.
        """
        if not isinstance(runtime_lease, RuntimeLease):
            return outcome_failed(
                CONTRACT_FAILED, "authorization", "lease_required",
                "授权需要已绑定的 RuntimeLease。",
            )
        if runtime_lease.plan_digest != plan.digest:
            return outcome_failed(
                CONTRACT_FAILED, "authorization", "lease_plan_mismatch",
                "租约绑定的计划哈希与待授权计划不一致。",
            )
        effects = requested_effects if isinstance(requested_effects, dict) else {}
        level = int(effects.get("level", 1))
        if level < 1 or level > 4:
            return outcome_failed(
                CONTRACT_FAILED, "authorization", "invalid_effect_level",
                "副作用等级必须为 1..4。",
            )
        if level > plan.risk_level:
            return outcome_failed(
                POLICY_DENIED, "authorization", "effect_exceeds_plan",
                "请求的副作用等级超过计划声明的风险等级。",
            )
        if level == 4 and not self.destructive_enabled:
            return outcome_failed(
                POLICY_DENIED, "authorization", "destructive_disabled",
                "破坏性编辑/覆盖发布默认禁用。",
            )
        if level == 4 and not effects.get("transactional"):
            return outcome_failed(
                POLICY_DENIED, "authorization", "destructive_not_atomic",
                "破坏性操作必须提供可证明的事务回滚。",
            )
        grant = self._build_grant(actor, plan, runtime_lease, level, effects)
        return outcome_succeeded(
            "authorization", "授权已绑定。",
            details={"grant": grant},
        )

    def _build_grant(self, actor: RequestEnvelope, plan: VerifiedPlan,
                     lease: RuntimeLease, level: int,
                     effects: Dict[str, Any]) -> AuthorizationGrant:
        now = time.time()
        input_identities = tuple(
            str(item) for item in (effects.get("inputs") or ())
        )
        output_identities = tuple(
            str(item) for item in (effects.get("outputs") or ())
        )
        return AuthorizationGrant(
            grant_id=str(uuid.uuid4()),
            run_id=lease.run_id,
            plan_digest=plan.digest,
            actor=CallerIdentity(
                user_id=actor.caller.user_id,
                tenant_id=actor.caller.tenant_id,
                role=actor.caller.role,
                data_scope=tuple(actor.caller.data_scope),
                client_kind=actor.caller.client_kind,
            ),
            input_identities=input_identities,
            output_identities=output_identities,
            allowed_side_effect_level=level,
            lease_id=lease.lease_id,
            lease_epoch=lease.epoch,
            expires_at=now + self.grant_lifetime_seconds,
            nonce=str(uuid.uuid4()),
            version=1,
        )

    def check_grant(self, grant: AuthorizationGrant,
                    lease: RuntimeLease, plan_digest: str,
                    now: Optional[float] = None) -> Outcome:
        """Validate a persisted grant against current bindings (§6.6).

        Used on resume / callback paths: the grant must still match the lease
        (id + epoch), the plan digest and its expiry window.
        """
        current = time.time() if now is None else float(now)
        if grant.lease_id != lease.lease_id or grant.lease_epoch != lease.epoch:
            return outcome_failed(
                POLICY_DENIED, "authorization", "grant_lease_mismatch",
                "授权与当前租约不匹配。",
            )
        if grant.plan_digest != plan_digest:
            return outcome_failed(
                POLICY_DENIED, "authorization", "grant_plan_mismatch",
                "授权绑定的计划哈希已变化。",
            )
        if grant.expires_at <= current:
            return outcome_failed(
                POLICY_DENIED, "authorization", "grant_expired",
                "授权已过期。",
            )
        return outcome_succeeded("authorization", "授权有效。")
