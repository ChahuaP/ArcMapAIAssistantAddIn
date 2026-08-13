# -*- coding: utf-8 -*-
"""Closed Acceptance Profile registry — the single fact source for acceptance.

Shared by the Python 3 Gateway seal (``acceptance_contract.derive``) and the
Python 2 runtime dispatcher (``semantic_acceptance.evaluate``).  Each
registered semantic effect is bound to exactly one evaluator profile, the
evidence keys its seal must produce, whether it produces a declared output, and
the closed set of supported parameter combinations.

The catalog loader binds every registered capability to the profile of its
primary semantic effect and rejects any capability whose effect or parameter
combination has no provable profile — at registration, before authorization.
The model never chooses or alters a profile; it only selects closed option ids.
"""
from __future__ import absolute_import

try:
    string_types = (str, unicode)
except NameError:  # Python 3
    string_types = (str,)


class ProfileError(ValueError):
    pass


LENGTH_UNITS = frozenset(("meters", "kilometers", "map_units", "degrees"))
SUPPORTED_OVERLAY_METHODS = frozenset((
    "intersect", "union", "clip", "erase", "symmetrical_difference",
    "identity", "update",
))
SUPPORTED_JOIN_MATCHES = frozenset(("intersect", "within", "contain"))
SUPPORTED_STAT_OPS = frozenset(("sum", "count", "mean", "min", "max"))
# map_units is only meaningful on a known projected CRS; degrees only on a
# geographic CRS. meters/kilometers require a real comparison CRS strategy.
LINEAR_QUANTITY_UNITS = frozenset(("meters", "kilometers", "map_units", "degrees"))


_DATASET_ROLE_VOCABULARY = frozenset((
    "source", "sources", "target", "selector", "join", "subject",
))


def _validate_role_names(roles, label):
    """Validate a role tuple against the closed vocabulary; fail fast."""
    seen = set()
    for r in roles:
        if not isinstance(r, string_types) or not r:
            raise ProfileError("%s role name must be a non-empty string: %r" % (label, r))
        if r not in _DATASET_ROLE_VOCABULARY:
            raise ProfileError("%s role %r is not in the closed vocabulary %s"
                               % (label, r, sorted(_DATASET_ROLE_VOCABULARY)))
        if r in seen:
            raise ProfileError("%s role %r is duplicated" % (label, r))
        seen.add(r)
    return tuple(roles)


class AcceptanceProfile(object):
    """One effect's closed acceptance contract.

    ``roles`` are REQUIRED dataset-parameter roles (always validated at catalog
    load).  ``optional_roles`` are dataset-parameter roles that MAY appear in a
    given capability variant; when present they are validated with the same
    canonical gate, but their absence is not an error.
    """

    __slots__ = ("effect", "evaluator", "requires_output", "roles", "optional_roles",
                 "supported", "needs_crs_strategy", "evidence_rule", "requires_producing_step")

    def __init__(self, effect, evaluator, requires_output, roles=(), optional_roles=(),
                 supported=None, needs_crs_strategy=False, evidence_rule=None,
                 requires_producing_step=False):
        self.effect = effect
        self.evaluator = evaluator
        self.requires_output = bool(requires_output)
        self.roles = _validate_role_names(roles, "required")
        self.optional_roles = _validate_role_names(optional_roles, "optional")
        overlap = set(self.roles) & set(self.optional_roles)
        if overlap:
            raise ProfileError(
                "effect %s has overlapping required and optional roles: %s"
                % (effect, sorted(overlap)))
        self.supported = dict(supported or {})
        self.needs_crs_strategy = bool(needs_crs_strategy)
        if evidence_rule is None:
            evidence_rule = lambda predicate: ()
        self.evidence_rule = evidence_rule
        self.requires_producing_step = bool(requires_producing_step)


def _p(effect, evaluator, requires_output, roles=(), optional_roles=(),
       supported=None, needs_crs_strategy=False, evidence_rule=None,
       requires_producing_step=False):
    return AcceptanceProfile(effect, evaluator, requires_output, roles, optional_roles,
                             supported, needs_crs_strategy, evidence_rule,
                             requires_producing_step)


def _const_keys(keys):
    """Evidence rule that always requires the same keys."""
    return lambda predicate: keys


_INPLACE_LINEAGE = _const_keys(("lineage", "parameter_summary"))

# The closed registry.  Every catalog primary effect MUST be present here.
PROFILES = {
    "copy": _p("copy", "copy", True, ["source"]),
    "merge": _p("merge", "merge", True, ["sources"]),
    "append": _p("append", "append", False, ["sources", "target"],
                 evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "aggregate": _p("aggregate", "aggregate", True, ["source"],
                    supported={"statistics_operator": SUPPORTED_STAT_OPS},
                    evidence_rule=_const_keys(("aggregate", "required_fields"))),
    "spatial_join": _p("spatial_join", "spatial_join", True, ["target", "join"],
                       supported={"match_option": SUPPORTED_JOIN_MATCHES},
                       evidence_rule=_const_keys(("join_spec", "required_fields"))),
    "overlay": _p("overlay", "overlay", True, ["sources"],
                  supported={"method": SUPPORTED_OVERLAY_METHODS},
                  evidence_rule=_const_keys(("overlay",))),
    "buffer": _p("buffer", "buffer", True, ["source"],
                 supported={"unit": LINEAR_QUANTITY_UNITS}, needs_crs_strategy=True,
                 evidence_rule=_const_keys(("crs_strategy",))),
    "spatial_filter": _p("spatial_filter", "spatial_filter", False,
                         ["target", "selector"], supported={"overlap_type": frozenset((
                             "intersect", "contain", "within", "touch", "overlap", "cross",
                             "within_a_distance"))}, needs_crs_strategy=True,
                         evidence_rule=lambda p: (("crs_strategy",) if p.get("overlap_type") == "within_a_distance" else ())
                         + ("lineage", "parameter_summary"),
                         requires_producing_step=True),
    "project": _p("project", "project", True, ["source"]),
    "define_projection": _p("define_projection", "define_projection", False, ["target"],
                            evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "field_add": _p("field_add", "field_add", False, ["target"],
                    evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "field_delete": _p("field_delete", "field_delete", False, ["target"],
                       evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "field_update": _p("field_update", "field_update", False, ["target"],
                       evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "feature_create": _p("feature_create", "feature_create", True, []),
    "feature_append": _p("feature_append", "feature_append", False, ["target"],
                         evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "repair": _p("repair", "repair", False, ["target"],
                 evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "add_xy": _p("add_xy", "add_xy", False, ["target"],
                 evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "attribute_filter": _p("attribute_filter", "attribute_filter", False, ["target"],
                           evidence_rule=_INPLACE_LINEAGE, requires_producing_step=True),
    "artifact_export": _p("artifact_export", "artifact_export", True, []),
    "inspect": _p("inspect", "inspect", False, roles=(), optional_roles=("target",)),
    "source_preserved": _p("source_preserved", "source_preserved", False, ["subject"],
                           evidence_rule=_const_keys(("source_manifest",))),
    "map_change": _p("map_change", "map_change", True, [],
                     evidence_rule=_const_keys(("pre_state",))),
    "layout_change": _p("layout_change", "layout_change", True, [],
                        evidence_rule=_const_keys(("pre_state",))),
}


def get_profile(effect):
    """Return the profile for an effect or raise (no silent fallback)."""
    if not isinstance(effect, string_types):
        raise ProfileError("effect kind must be a string")
    try:
        return PROFILES[effect]
    except KeyError:
        raise ProfileError("no acceptance profile registered for effect: %s" % effect)


def has_profile(effect):
    return isinstance(effect, string_types) and effect in PROFILES


def registered_effects():
    return tuple(sorted(PROFILES))


_SINGULAR_ROLES = frozenset(("source", "target", "selector", "join", "subject"))


def _validate_binding_object(binding, effect_kind, role):
    """Validate a binding object has exactly one key 'parameter' with a non-empty string."""
    if not isinstance(binding, dict) or set(binding.keys()) != {"parameter"}:
        raise ProfileError(
            "effect %s role %s binding must be exactly {\"parameter\": \"name\"}"
            % (effect_kind, role))
    param = binding["parameter"]
    if not isinstance(param, string_types) or not param:
        raise ProfileError(
            "effect %s role %s binding parameter must be a non-empty string"
            % (effect_kind, role))
    return param


def _validate_singular_layer_schema(schema, effect_kind, role, param):
    """Singular role parameter must be exactly type=string, x-geopilot-kind=layer."""
    if not isinstance(schema, dict):
        raise ProfileError(
            "effect %s role %s parameter %s not in parameters_schema"
            % (effect_kind, role, param))
    if schema.get("x-geopilot-kind") != "layer":
        raise ProfileError(
            "effect %s role %s parameter %s must have x-geopilot-kind=layer"
            % (effect_kind, role, param))
    if schema.get("type") != "string":
        raise ProfileError(
            "effect %s role %s parameter %s must have type=string"
            % (effect_kind, role, param))


def _validate_sources_layer_schema(schema, effect_kind, param):
    """Sources role parameter: scalar layer-string OR non-empty layer-string-array."""
    if not isinstance(schema, dict):
        raise ProfileError(
            "effect %s role sources parameter %s not in parameters_schema"
            % (effect_kind, param))
    if schema.get("x-geopilot-kind") != "layer":
        raise ProfileError(
            "effect %s role sources parameter %s must have x-geopilot-kind=layer"
            % (effect_kind, param))
    st = schema.get("type")
    if st == "string":
        return
    if st == "array":
        items = schema.get("items")
        if not isinstance(items, dict) or items.get("type") != "string":
            raise ProfileError(
                "effect %s role sources array parameter %s items must have type=string"
                % (effect_kind, param))
        min_items = schema.get("minItems")
        if not isinstance(min_items, int) or isinstance(min_items, bool) or min_items < 1:
            raise ProfileError(
                "effect %s role sources array parameter %s must have integer minItems >= 1"
                % (effect_kind, param))
        return
    raise ProfileError(
        "effect %s role sources parameter %s has unsupported type %s"
        % (effect_kind, param, st))


def _validate_role_binding(effect, role, params_schema, effect_kind):
    """Validate ONE role binding's canonical shape + parameter schema.  No mutation."""
    binding = effect.get(role)
    if role in _SINGULAR_ROLES:
        param = _validate_binding_object(binding, effect_kind, role)
        schema = params_schema.get(param)
        _validate_singular_layer_schema(schema, effect_kind, role, param)
    elif role == "sources":
        if not isinstance(binding, list) or not binding:
            raise ProfileError(
                "effect %s role sources must be a non-empty array of {\"parameter\": \"name\"}"
                % effect_kind)
        seen_params = set()
        for item in binding:
            param = _validate_binding_object(item, effect_kind, "sources")
            if param in seen_params:
                raise ProfileError(
                    "effect %s role sources has duplicate parameter %s"
                    % (effect_kind, param))
            seen_params.add(param)
            schema = params_schema.get(param)
            _validate_sources_layer_schema(schema, effect_kind, param)
    else:
        raise ProfileError("effect %s has unrecognized role %s" % (effect_kind, role))


def validate_capability_binding(capability_contract):
    """Bind EVERY declared semantic effect of a capability to a profile at load.

    Validation only — never mutates the contract.  Rejects non-canonical role
    binding shapes, missing parameters, wrong schema types/cardinalities, and
    missing producing-step obligations for state-changing non-output capabilities.
    """
    if not isinstance(capability_contract, dict):
        raise ProfileError("capability_contract is required")
    effects = capability_contract.get("semantic_effects") or []
    if not effects:
        raise ProfileError("capability declares no semantic effect")
    params_schema = (capability_contract.get("parameters_schema") or {}).get("properties", {})
    side_effects = (capability_contract.get("side_effects") or "")
    output_kind = ((capability_contract.get("outputs") or {}).get("kind") or "")
    non_output_state_changing = (side_effects != "read_only" and output_kind in ("none", ""))
    profiles = []
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict) or not effect.get("kind"):
            raise ProfileError("capability semantic_effects[%d] has no kind" % index)
        profile = get_profile(effect["kind"])
        if non_output_state_changing and profile.requires_output:
            raise ProfileError(
                "capability effect %s declares requires_output but the capability "
                "outputs.kind is none (in-place)" % effect["kind"])
        if non_output_state_changing and not profile.requires_producing_step:
            raise ProfileError(
                "capability effect %s is state-changing (side_effects=%s) with no "
                "declared output but its profile does not require a producing step"
                % (effect["kind"], side_effects))
        # Validate canonical role binding shapes + parameter schemas for EVERY
        # profile — not only requires_producing_step.  Output-producing and
        # read-only effects (buffer source, spatial_join target/join, copy/project
        # source) must pass the same canonical gate.
        for role in profile.roles:
            _validate_role_binding(effect, role, params_schema, effect["kind"])
        # Optional roles: validate only when explicitly present in the effect.
        for role in profile.optional_roles:
            if effect.get(role) is not None:
                _validate_role_binding(effect, role, params_schema, effect["kind"])
        profiles.append(profile)
    return profiles


def bound_effect_kinds(profiles):
    """The effect kinds a list of bound profiles covers."""
    return tuple(p.effect for p in profiles)


def param_supported(profile, parameter, value):
    """Closed-set parameter check for a profile (e.g. overlay method)."""
    allowed = profile.supported.get(parameter)
    if allowed is None:
        return True
    return value in allowed


# --- CRS / linear-Quantity comparison strategy (shared by seal + Py2 evaluator) ---
#
# The CRS type and meters-per-linear-unit are runtime-verified by ArcPy Describe
# and sealed into the context by the gateway — the strategy never *infers* the
# unit from a CRS name.  meters/kilometers on a geographic CRS is not
# independently provable without projection and is therefore rejected at seal;
# a projected CRS uses its sealed meters-per-unit to convert the Quantity into
# native CRS units (works for meter, foot and survey-foot projections alike).

def comparison_strategy(source_crs, quantity):
    """Resolve a buffer/distance value in the source CRS's native linear unit.

    Returns ``{"mode": ..., "buffer_value": <value in source CRS units>}`` the
    Py2 evaluator buffers/distance-compares natively (source and result share
    the CRS).  Raises ``ProfileError`` when the combination cannot be
    independently proven, so the seal rejects before execution — never a runtime
    Unresolved, never an approximation.

    ``source_crs`` is ``{"type": "Geographic"|"Projected"|..., "meters_per_unit":
    float|None, "name": str}`` (type + meters_per_unit come from ArcPy Describe).
    """
    if not isinstance(quantity, dict) or not quantity.get("unit"):
        raise ProfileError("a sealed linear Quantity is required")
    unit = quantity["unit"]
    if unit not in LINEAR_QUANTITY_UNITS:
        raise ProfileError("unsupported linear Quantity unit: %s" % unit)
    if not isinstance(source_crs, dict):
        raise ProfileError("a sealed source CRS is required for linear comparison")
    crs_type = (source_crs.get("type") or u"")
    is_geographic = "geographic" in crs_type.lower()
    is_projected = "projected" in crs_type.lower()
    if not is_geographic and not is_projected:
        raise ProfileError("source CRS type is not sealed (geographic/projected)")
    value = float(quantity.get("value") or 0.0)

    if is_geographic:
        if unit == "degrees":
            return {"mode": "native_degrees", "buffer_value": value, "source_crs": source_crs}
        # meters/kilometers/map_units on a geographic CRS need projection to be
        # provable; the seal requires a prior project step instead.
        raise ProfileError("a %s Quantity on a geographic CRS requires projection first"
                           % unit)

    # Projected CRS: convert meters/kilometers via the sealed meters-per-unit.
    if unit in ("meters", "kilometers"):
        meters = value * (1000.0 if unit == "kilometers" else 1.0)
        mpu = source_crs.get("meters_per_unit")
        if not isinstance(mpu, (int, float)) or isinstance(mpu, bool) or mpu <= 0:
            raise ProfileError("projected CRS linear unit (meters_per_unit) is not sealed")
        return {"mode": "native_projected", "buffer_value": meters / float(mpu),
                "meters": meters, "meters_per_unit": float(mpu), "source_crs": source_crs}
    if unit == "map_units":
        return {"mode": "native_projected", "buffer_value": value, "source_crs": source_crs}
    # degrees on a projected CRS is invalid.
    raise ProfileError("a degrees Quantity is invalid on a projected CRS")

