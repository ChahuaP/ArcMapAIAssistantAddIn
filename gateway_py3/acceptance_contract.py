"""Sealed, executable acceptance obligations derived from plan and capability.

The acceptance contract is the single seal point (§4).  Every output's
acceptance specification travels inside one ``bindings`` object per rule — the
only structure the Python 2 evaluator receives.  Nothing the evaluator needs
lives at the rule top level, so the rule ABI is uniform across kinds.

The specification is derived from the sealed ``VerifiedPlan`` (its declared
outputs and producing step), the frozen ``ContextSnapshot`` (real per-dataset
field specs, identity fields and content manifests), the server-bound input
entities and the producing capability contract.  The task predicate is never
extended to carry acceptance detail: it expresses user intent only.

Each rule seals enough independent evidence for the Python 2 evaluator to
re-open the datasets after execution and prove the semantic effect without any
execution receipt:

* per-dataset identity fields and full canonical FieldSpec semantics, never a
  union across heterogeneous sources;
* ``LineageFact`` (output, inputs, capability, parameter digest), the source
  list and the parameter summary;
* kind-specific sealed specs (aggregate group/statistics, spatial-join
  correspondence, overlay field mapping) computed from the producing step and
  capability contract;
* the pre-execution content manifest, including normalized geometry and raster
  cell digests, for ``source_preserved``;
* the frozen map/layout pre-state, for unified map-state acceptance.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from .kernel.contracts import VerifiedPlan, digest
from shared_runtime.acceptance_profile import (
    PROFILES, ProfileError, comparison_strategy, get_profile, param_supported,
)


class AcceptanceContractError(ValueError):
    pass


# Roles that bind a concrete dataset (input or output) inside one rule.
_DATASET_ROLES = ("source", "target", "selector", "join", "subject")
_FILTER_KINDS = {"attribute_filter", "spatial_filter", "artifact_export"}
_MAP_KINDS = {"map_change", "layout_change"}
_AGG_KINDS = {"aggregate"}
_JOIN_KINDS = {"spatial_join"}
_OVERLAY_KINDS = {"overlay"}
_COPY_KINDS = {"copy", "merge", "append"}


def _field_spec_doc(field: Any) -> Dict[str, Any]:
    """Seal the complete canonical ABI semantics of one field (7 fields)."""
    return {
        "name": field.name,
        "type": field.dtype,
        "nullable": bool(field.nullable),
        "length": field.length,
        "precision": field.precision,
        "scale": field.scale,
        "domain": list(field.domain),
    }


def _layer_evidence(layer: Any) -> Dict[str, Any]:
    """Per-dataset frozen evidence; identity fields are never unioned."""
    identity = layer.identity
    return {
        "name": identity.name,
        "layer_ref": identity.layer_ref,
        "path": identity.data_source,
        "geometry_type": layer.geometry_type,
        "coordinate_system": layer.coordinate_system,
        "fields": [_field_spec_doc(field) for field in layer.fields],
        # Identity fields belong to *this* dataset only.
        "identity_fields": list(layer.identity_fields),
        "source_content_digest": layer.source_content_digest,
        "feature_manifest_digest": layer.feature_manifest_digest,
        "raster_content_digest": layer.raster_content_digest,
        "crs_type": layer.crs_type,
        "meters_per_unit": layer.meters_per_unit,
    }


def _evidence_index(context: Any) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for layer in getattr(context, "layers", ()):
        evidence = _layer_evidence(layer)
        identity = layer.identity
        for key in (identity.name, identity.layer_ref,
                    getattr(identity, "data_source", None), layer.long_name):
            if isinstance(key, str) and key:
                index[key] = evidence
    return index


def _step_index(plan: VerifiedPlan) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    outputs_by_id: Dict[str, Any] = {}
    step_by_output: Dict[str, Any] = {}
    for step in plan.workflow:
        for output in step.declared_outputs:
            outputs_by_id[output.output_id] = output
            step_by_output[output.output_id] = step
    return outputs_by_id, step_by_output


_SINGULAR_DATASET_ROLES = frozenset(("source", "target", "selector", "join", "subject"))


def _role_param_name(semantic_effect: Dict[str, Any], role: str):
    """Extract parameter name(s) for a role from a canonical semantic_effect.

    Singular roles return a single ``str``; ``sources`` returns a ``list[str]``.
    Returns None if the binding is absent or malformed.  Every binding element
    must be exactly ``{"parameter": non-empty-string}`` — extra keys, empty
    names, or partially malformed arrays are rejected (not filtered).
    """
    binding = semantic_effect.get(role)
    if role in _SINGULAR_DATASET_ROLES:
        if not isinstance(binding, dict) or set(binding.keys()) != {"parameter"}:
            return None
        param = binding["parameter"]
        if not isinstance(param, str) or not param:
            return None
        return param
    if role == "sources":
        if not isinstance(binding, list) or not binding:
            return None
        names = []
        for item in binding:
            if not isinstance(item, dict) or set(item.keys()) != {"parameter"} \
                    or not isinstance(item.get("parameter"), str) or not item["parameter"]:
                return None
            names.append(item["parameter"])
        if len(set(names)) != len(names):
            return None  # reject duplicate parameter names
        return names if names else None
    return None


def _match_step_by_effect(step: Any, semantic_effect: Dict[str, Any],
                          predicate: Dict[str, Any], entity_to_ref: Dict[str, str],
                          profile_roles: Tuple[str, ...],
                          params_schema: Dict[str, Any]) -> bool:
    """Exact, schema/cardinality-aware match against canonical role bindings.

    For each profile role, the step's declared parameter value(s) must EQUAL the
    predicate entity's plan reference(s) with the correct schema type.  No
    scanning, no heuristics, no subset/intersection.
    """
    step_args = dict(getattr(step, "arguments", {}) or {})
    for role in profile_roles:
        params = _role_param_name(semantic_effect, role)
        if params is None:
            return False
        param_list = [params] if isinstance(params, str) else params
        is_sources = (role == "sources")
        # Predicate entity → reference(s).  Enforce cardinality + bijection.
        pred_entity = predicate.get(role)
        if pred_entity is None:
            return False
        if isinstance(pred_entity, list):
            if not is_sources:
                return False
            if len(set(pred_entity)) != len(pred_entity):
                return False  # duplicate entity ids
            pred_ref_list = []
            for eid in pred_entity:
                if not isinstance(eid, str) or not eid:
                    return False
                ref = entity_to_ref.get(eid)
                if ref is None:
                    return False
                pred_ref_list.append(ref)
            # Bijective: different entities must not resolve to the same ref.
            if len(set(pred_ref_list)) != len(pred_ref_list):
                return False
            pred_refs = set(pred_ref_list)
        elif isinstance(pred_entity, str):
            if is_sources or not pred_entity:
                return False
            ref = entity_to_ref.get(pred_entity)
            if ref is None:
                return False
            pred_refs = {ref}
        else:
            return False
        # Collect step refs from ALL bound parameters; enforce schema + no dup.
        step_value_list = []
        for param_name in param_list:
            schema = params_schema.get(param_name)
            if not isinstance(schema, dict) or schema.get("x-geopilot-kind") != "layer":
                return False
            st = schema.get("type")
            v = step_args.get(param_name)
            if st == "string":
                if not isinstance(v, str) or not v:
                    return False
                step_value_list.append(v)
            elif st == "array" and is_sources:
                items = schema.get("items")
                if not isinstance(items, dict) or items.get("type") != "string":
                    return False
                min_items = schema.get("minItems")
                if not isinstance(min_items, int) or isinstance(min_items, bool) or min_items < 1:
                    return False
                if not isinstance(v, list) or not v:
                    return False
                if any(not isinstance(item, str) or not item for item in v):
                    return False
                step_value_list.extend(v)
            elif st == "array" and not is_sources:
                return False  # singular role must not bind an array parameter
            else:
                return False  # unsupported type
        if len(set(step_value_list)) != len(step_value_list):
            return False  # duplicate refs across parameters
        step_refs = set(step_value_list)
        if pred_refs != step_refs:
            return False
    return True


def _find_step_by_effect(plan: Any, catalog: Any, kind: str,
                         requirement_id: str, predicate: Dict[str, Any],
                         entity_to_ref: Dict[str, str]) -> Any:
    """Find the UNIQUE plan step whose catalog-declared semantic_effect for
    ``kind`` binds its role→parameters to values that EXACTLY match the
    predicate's bound entity references.  Raises ``AcceptanceContractError`` for
    zero or multiple matches.
    """
    getter = getattr(catalog, "get", None)
    if getter is None:
        raise AcceptanceContractError(
            "requirement %s (%s) requires a producing step but no catalog is available"
            % (requirement_id, kind))
    try:
        profile = get_profile(kind)
    except ProfileError:
        profile = None
    if profile is None:
        raise AcceptanceContractError(
            "requirement %s (%s) has no acceptance profile" % (requirement_id, kind))
    candidates = []
    for step in getattr(plan, "workflow", ()):
        contract = getter(getattr(step, "operation", ""))
        if not isinstance(contract, dict):
            continue
        effects = (contract.get("capability_contract") or {}).get("semantic_effects") or []
        semantic_effect = next((e for e in effects
                                if isinstance(e, dict) and e.get("kind") == kind), None)
        if semantic_effect is None:
            continue
        if _match_step_by_effect(step, semantic_effect, predicate, entity_to_ref,
                                 profile.roles,
                                 (contract.get("parameters_schema") or {}).get("properties", {})):
            candidates.append(step)
    if len(candidates) == 0:
        raise AcceptanceContractError(
            "requirement %s (%s) has no producing step whose semantic-effect "
            "role→parameter values match the predicate entities"
            % (requirement_id, kind))
    if len(candidates) > 1:
        raise AcceptanceContractError(
            "requirement %s (%s) is ambiguous: %d steps match"
            % (requirement_id, kind, len(candidates)))
    return candidates[0]


def _task_outputs_by_id(task_contract: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result = {}
    for output in task_contract.get("outputs", []) or []:
        if isinstance(output, dict) and isinstance(output.get("output_id"), str):
            result[output["output_id"]] = output
    return result


def _sealed_input_bindings(input_bindings: Iterable[Any],
                           evidence: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    sealed: Dict[str, Dict[str, Any]] = {}
    for binding in input_bindings:
        name = getattr(binding, "name", None)
        if not isinstance(name, str) or not name:
            continue
        item = {key: value for key, value in {
            "path": getattr(binding, "path", None),
            "layer_ref": getattr(binding, "layer_ref", None),
        }.items() if isinstance(value, str) and value}
        layer_evidence = (evidence.get(name)
                          or evidence.get(getattr(binding, "layer_ref", None))
                          or evidence.get(getattr(binding, "path", None)))
        if layer_evidence is not None:
            full = dict(layer_evidence)
            full["path"] = item.get("path", layer_evidence.get("path"))
            item = full
        if item:
            sealed[name] = item
    return sealed


def _dataset_binding(entity: Optional[str],
                     sealed_inputs: Dict[str, Dict[str, Any]],
                     outputs_by_id: Dict[str, Any]) -> Optional[Any]:
    """Resolve one predicate role to a sealed input dict or the output marker.

    Output entities resolve to the literal ``"__output__"`` marker so the Py2
    evaluator's ``_dataset`` rewrites them to the probe document's canonical
    path.  Input entities resolve to their full per-dataset evidence dict.
    """
    if not isinstance(entity, str) or not entity:
        return None
    if entity in sealed_inputs:
        return dict(sealed_inputs[entity])
    if entity in outputs_by_id:
        return "__output__"
    return None


def _lineage_from_rule(step: Any, output_id: str, predicate: Dict[str, Any],
                       profile: Any) -> Dict[str, Any]:
    """Exact, non-polluting lineage from the predicate's profile-role entities.

    ``input_ids`` is built directly from the predicate's declared dataset roles
    (per the production profile), NOT by scanning step arguments — so an
    unrelated third layer in the step can never leak into this rule's lineage.
    """
    input_ids = set()
    for role in profile.roles:
        entity = predicate.get(role)
        if isinstance(entity, list):
            for eid in entity:
                if isinstance(eid, str):
                    input_ids.add(eid)
        elif isinstance(entity, str):
            input_ids.add(entity)
    arguments = dict(getattr(step, "arguments", {}) or {})
    return {
        "output_id": output_id,
        "capability_id": getattr(step, "operation", None),
        "input_ids": sorted(input_ids),
        "parameter_digest": digest({"operation": getattr(step, "operation", ""),
                                    "arguments": arguments}),
    }


def _require_identity(binding: Dict[str, Any], role: str, requirement_id: str) -> None:
    if "__output__" in binding:
        return
    if not binding.get("identity_fields"):
        raise AcceptanceContractError(
            "requirement %s lacks frozen stable identity fields on %s"
            % (requirement_id, role))


def derive(task_contract: Dict[str, Any], plan: VerifiedPlan,
           input_bindings: Iterable[Any] = (), context: Any = None,
           catalog: Any = None) -> Dict[str, Any]:
    """Create the only acceptance contract; receipts are intentionally absent."""
    if not isinstance(task_contract, dict):
        raise AcceptanceContractError("sealed task_contract is required")
    requirements = task_contract.get("requirements")
    if not isinstance(requirements, list):
        raise AcceptanceContractError("task_contract.requirements is required")
    outputs_by_id, step_by_output = _step_index(plan)
    task_outputs = _task_outputs_by_id(task_contract)
    evidence = _evidence_index(context) if context is not None else {}
    sealed_inputs = _sealed_input_bindings(input_bindings, evidence)
    # Build the mapping from sealed entity ids to plan-step argument references
    # (used by the producing-step matcher to verify a step's arguments reference
    # exactly the predicate's bound dataset entities).
    entity_to_ref: Dict[str, str] = {}
    for entity in task_contract.get("input_entities", []) or []:
        if isinstance(entity, dict) and entity.get("entity_id") and entity.get("reference"):
            entity_to_ref[entity["entity_id"]] = entity["reference"]

    rules = []
    for item in requirements:
        if not isinstance(item, dict):
            raise AcceptanceContractError("task requirement is malformed")
        requirement_id = item.get("requirement_id")
        predicate = item.get("predicate")
        if not isinstance(requirement_id, str) or not isinstance(predicate, dict):
            raise AcceptanceContractError("task requirement is malformed")
        rules.append(_derive_rule(requirement_id, predicate, sealed_inputs,
                                  outputs_by_id, step_by_output, task_outputs,
                                  evidence, context, catalog, plan, entity_to_ref))

    document = {"plan_digest": plan.digest,
                "task_contract_digest": digest(task_contract),
                "inputs": dict(sorted(sealed_inputs.items())),
                "rules": rules}
    document["digest"] = digest(document)
    return document


def _derive_rule(requirement_id: str, predicate: Dict[str, Any],
                 sealed_inputs: Dict[str, Dict[str, Any]],
                 outputs_by_id: Dict[str, Any],
                 step_by_output: Dict[str, Any],
                 task_outputs: Dict[str, Dict[str, Any]],
                 evidence: Dict[str, Dict[str, Any]],
                 context: Any, catalog: Any, plan: Any,
                 entity_to_ref: Dict[str, str]) -> Dict[str, Any]:
    kind = predicate.get("kind")
    subject = predicate.get("subject")
    output_id = subject if subject in outputs_by_id else None

    bindings: Dict[str, Any] = {}
    for role in _DATASET_ROLES:
        binding = _dataset_binding(predicate.get(role), sealed_inputs, outputs_by_id)
        if binding is not None:
            bindings[role] = binding
    sources_entity = predicate.get("sources")
    if isinstance(sources_entity, list):
        bound_sources = []
        for entity in sources_entity:
            binding = _dataset_binding(entity, sealed_inputs, outputs_by_id)
            if binding is not None:
                bound_sources.append(binding)
        if bound_sources:
            bindings["sources"] = bound_sources

    producing_step = step_by_output.get(output_id) if output_id else None
    if producing_step is None:
        try:
            _profile = get_profile(kind)
        except ProfileError:
            _profile = None
        if _profile is not None and _profile.requires_producing_step:
            # State-changing non-output effect (append, field_update, repair,
            # add_xy, attribute_filter, spatial_filter, …): MUST uniquely bind
            # to exactly one producing step whose real arguments reference the
            # predicate's bound dataset entities.  Zero or multiple candidates →
            # AcceptanceContractError (fail closed before authorization).
            producing_step = _find_step_by_effect(plan, catalog, kind, requirement_id,
                                                  predicate, entity_to_ref)
        # inspect / source_preserved don't mutate → no producing step needed.
    if producing_step is not None:
        try:
            _prof = get_profile(kind)
        except ProfileError:
            _prof = None
        if _prof is not None:
            lineage_output = output_id or predicate.get("target") or predicate.get("subject") or "in_place"
            bindings["lineage"] = _lineage_from_rule(producing_step, lineage_output, predicate, _prof)
        bindings["parameter_summary"] = {
            "operation": getattr(producing_step, "operation", ""),
            "arguments": dict(getattr(producing_step, "arguments", {}) or {}),
            "digest": digest(dict(getattr(producing_step, "arguments", {}) or {})),
        }
        bindings["source_list"] = list(bindings["lineage"]["input_ids"])

    _seal_kind_specific(bindings, kind, predicate, output_id, task_outputs,
                       producing_step, catalog, context, requirement_id)

    # The profile's evidence_rule is parameter-aware: it returns the binding
    # keys the evaluator REQUIRES for THIS specific predicate (e.g. spatial_filter
    # needs crs_strategy only for within_a_distance).  Every required key MUST
    # be present in the sealed bindings, else the rule is not independently
    # provable and is rejected before authorization.
    try:
        profile = get_profile(kind)
    except ProfileError as exc:  # defense-in-depth; catalog already rejected it
        raise AcceptanceContractError(
            "requirement %s has no acceptance profile: %s" % (requirement_id, exc))
    required_evidence = profile.evidence_rule(predicate)
    missing_evidence = [key for key in required_evidence if key not in bindings]
    if missing_evidence:
        raise AcceptanceContractError(
            "requirement %s (%s) is missing sealed evidence: %s"
            % (requirement_id, kind, missing_evidence))

    return {
        "proof_id": "acceptance:" + requirement_id,
        "predicate": predicate,
        "required": True,
        # The evaluator receives ONLY bindings; every input it needs is here.
        "bindings": bindings,
        "output_id": output_id,
    }


def _output_field_specs(subject: str, task_outputs: Dict[str, Dict[str, Any]]) -> Optional[list]:
    """Full canonical FieldSpec list declared on the subject output."""
    output = task_outputs.get(subject)
    if not isinstance(output, dict):
        return None
    fields = output.get("required_fields")
    if not isinstance(fields, list):
        return None
    return fields


def _capability_contract(catalog: Any, operation: Optional[str]) -> Optional[Dict[str, Any]]:
    if catalog is None or not isinstance(operation, str):
        return None
    getter = getattr(catalog, "get", None)
    if getter is None:
        return None
    try:
        contract = getter(operation)
    except Exception:
        return None
    return contract if isinstance(contract, dict) else None


def _static_field_specs(catalog: Any, operation: Optional[str]) -> list:
    contract = _capability_contract(catalog, operation)
    if not contract:
        return []
    fields = (contract.get("outputs") or {}).get("fields") or {}
    return list(fields.get("static_fields") or [])


def _seal_kind_specific(bindings: Dict[str, Any], kind: Optional[str],
                        predicate: Dict[str, Any], output_id: Optional[str],
                        task_outputs: Dict[str, Dict[str, Any]],
                        producing_step: Any, catalog: Any, context: Any,
                        requirement_id: str) -> None:
    if kind in _FILTER_KINDS:
        for role in ("source", "target", "selector", "subject"):
            binding = bindings.get(role)
            if binding is not None and "__output__" not in binding:
                _require_identity(binding, role, requirement_id)

    if kind in _AGG_KINDS | _JOIN_KINDS:
        # Full canonical FieldSpec list (7 fields each), not just names.
        fields = _output_field_specs(predicate.get("subject"), task_outputs)
        if not fields:
            raise AcceptanceContractError(
                "requirement %s does not bind a declared output with field specs"
                % requirement_id)
        bindings["required_fields"] = fields

    if kind in _AGG_KINDS:
        _seal_aggregate(bindings, predicate, producing_step, catalog, requirement_id)
    elif kind in _JOIN_KINDS:
        _seal_spatial_join(bindings, predicate, producing_step, catalog, requirement_id)
    elif kind in _OVERLAY_KINDS:
        _seal_overlay(bindings, predicate, producing_step, catalog, requirement_id)
    elif kind == "source_preserved":
        _seal_source_preserved(bindings, requirement_id)
    elif kind in _MAP_KINDS:
        _seal_map_prestate(bindings, context, requirement_id)
    elif kind in ("buffer", "spatial_filter"):
        _seal_linear_crs_strategy(bindings, predicate, requirement_id, kind)


def _seal_aggregate(bindings: Dict[str, Any], predicate: Dict[str, Any],
                    producing_step: Any, catalog: Any, requirement_id: str) -> None:
    dissolve_fields = predicate.get("dissolve_fields")
    if not isinstance(dissolve_fields, list) or not all(isinstance(x, str) and x for x in dissolve_fields):
        # An aggregate with no group fields is a global reduction: one group.
        dissolve_fields = []
    # Statistics: read from the producing step arguments if the capability
    # exposes them; an empty list means a geometry-only dissolve.
    arguments = dict(getattr(producing_step, "arguments", {}) or {}) if producing_step else {}
    statistics = []
    stats_arg = arguments.get("statistics")
    if isinstance(stats_arg, list):
        for item in stats_arg:
            if not isinstance(item, dict) or not item.get("field") or not item.get("operator"):
                raise AcceptanceContractError(
                    "requirement %s aggregate statistic is malformed" % requirement_id)
            operator = str(item["operator"]).lower()
            if operator not in get_profile("aggregate").supported["statistics_operator"]:
                raise AcceptanceContractError(
                    "requirement %s aggregate uses an unsupported statistic operator: %s"
                    % (requirement_id, operator))
            statistics.append({"field": item["field"], "operator": operator,
                               "output_field": item.get("output_field") or item["field"]})
    bindings["aggregate"] = {
        "dissolve_fields": list(dissolve_fields),
        "statistics": statistics,
        "tolerance": float(arguments.get("tolerance", 0.0) or 0.0),
    }


# Spatial join match options that the independent evaluator can recompute.
_SUPPORTED_JOIN_MATCHES = {"intersect", "within", "contain"}


def _seal_spatial_join(bindings: Dict[str, Any], predicate: Dict[str, Any],
                       producing_step: Any, catalog: Any, requirement_id: str) -> None:
    arguments = dict(getattr(producing_step, "arguments", {}) or {}) if producing_step else {}
    match_option = arguments.get("match_option") or "intersect"
    if match_option not in get_profile("spatial_join").supported["match_option"]:
        raise AcceptanceContractError(
            "requirement %s uses an unsupported spatial_join match option: %s"
            % (requirement_id, match_option))
    # The result must carry one stable id from each side so the evaluator can
    # prove target-join correspondence per result row.
    target_binding = bindings.get("target")
    join_binding = bindings.get("join")
    if not isinstance(target_binding, dict) or "__output__" in target_binding \
            or not isinstance(join_binding, dict) or "__output__" in join_binding:
        raise AcceptanceContractError(
            "requirement %s spatial_join requires sealed target and join datasets"
            % requirement_id)
    bindings["join_spec"] = {
        "match_option": match_option,
        "target_identity": list(target_binding.get("identity_fields") or []),
        "join_identity": list(join_binding.get("identity_fields") or []),
        "join_fields": [f.get("name") for f in join_binding.get("fields") or [] if isinstance(f, dict)],
        # The spatial-join result carries the target's source OID in TARGET_FID
        # (a capability-declared static field); the evaluator maps result rows
        # back to target features through it.
        "target_fid_field": _target_fid_field(catalog, getattr(producing_step, "operation", None)),
    }


def _target_fid_field(catalog: Any, operation: Optional[str]) -> Optional[str]:
    for spec in _static_field_specs(catalog, operation):
        name = spec.get("name") if isinstance(spec, dict) else spec
        if isinstance(name, str) and name.upper() == "TARGET_FID":
            return name
    return None


def _seal_overlay(bindings: Dict[str, Any], predicate: Dict[str, Any],
                  producing_step: Any, catalog: Any, requirement_id: str) -> None:
    method = predicate.get("method")
    if not isinstance(method, str) or not method:
        raise AcceptanceContractError(
            "requirement %s overlay requires a sealed method" % requirement_id)
    if method not in get_profile("overlay").supported["method"]:
        raise AcceptanceContractError(
            "requirement %s overlay method %s has no provable acceptance profile"
            % (requirement_id, method))
    sources = bindings.get("sources")
    if not isinstance(sources, list) or len(sources) < 2:
        raise AcceptanceContractError(
            "requirement %s overlay requires at least two sealed sources" % requirement_id)
    # Per-source field namespace for attribute value lineage. Each source keeps
    # its own field list and identity, so the evaluator can prove which source
    # geometry a result row's attribute values came from.
    field_mapping = []
    for index, source in enumerate(sources):
        if "__output__" in source:
            continue
        field_mapping.append({
            "namespace": "source_%d" % index,
            "identity_fields": list(source.get("identity_fields") or []),
            "fields": [f.get("name") for f in source.get("fields") or [] if isinstance(f, dict)],
        })
    bindings["overlay"] = {"method": method,
                           "tolerance": float(predicate.get("tolerance", 0.0) or 0.0),
                           "field_mapping": field_mapping,
                           "static_fields": _static_field_specs(catalog, getattr(producing_step, "operation", None))}


def _seal_linear_crs_strategy(bindings: Dict[str, Any], predicate: Dict[str, Any],
                              requirement_id: str, kind: str) -> None:
    """Seal the CRS/Quantity comparison strategy for buffer / spatial_filter.

    The evaluator must buffer or distance-compare a linear Quantity in the
    correct unit.  Passing meters to ``Geometry.buffer`` on a geographic CRS
    would treat them as degrees; instead the seal records the source CRS and a
    comparison mode the runtime honors.  A missing source CRS or an unsupported
    unit/CRS combination is rejected here, at seal — never a runtime Unresolved.
    """
    quantity_key = "distance" if kind == "buffer" else "search_distance"
    quantity = predicate.get(quantity_key)
    if kind == "spatial_filter" and predicate.get("overlap_type") != "within_a_distance":
        # Non-distance spatial predicates need no Quantity strategy.
        if quantity is not None:
            raise AcceptanceContractError(
                "requirement %s carries %s on a non-distance predicate"
                % (requirement_id, quantity_key))
        return
    if not isinstance(quantity, dict):
        raise AcceptanceContractError(
            "requirement %s lacks a sealed linear Quantity" % requirement_id)
    source_role = "selector" if kind == "spatial_filter" else "source"
    source_binding = bindings.get(source_role) or bindings.get("source") or bindings.get("target")
    if not isinstance(source_binding, dict) or "__output__" in source_binding:
        raise AcceptanceContractError(
            "requirement %s needs a sealed %s dataset for CRS" % (requirement_id, source_role))
    # The CRS type and meters-per-unit are runtime-verified by ArcPy Describe
    # and sealed into the layer evidence; never inferred from the CRS name.
    source_crs = {"name": source_binding.get("coordinate_system"),
                  "type": source_binding.get("crs_type") or u"",
                  "meters_per_unit": source_binding.get("meters_per_unit")}
    try:
        strategy = comparison_strategy(source_crs, quantity)
    except ProfileError as exc:
        raise AcceptanceContractError(
            "requirement %s linear Quantity/CRS is not independently provable: %s"
            % (requirement_id, exc))
    bindings["crs_strategy"] = strategy


def _seal_source_preserved(bindings: Dict[str, Any], requirement_id: str) -> None:
    subject_binding = bindings.get("subject") or bindings.get("source")
    if not isinstance(subject_binding, dict) or "__output__" in subject_binding:
        raise AcceptanceContractError(
            "requirement %s source_preserved must bind a sealed input dataset"
            % requirement_id)
    manifest = {key: subject_binding.get(key) for key in
                ("source_content_digest", "feature_manifest_digest", "raster_content_digest")}
    if not any(manifest.values()):
        raise AcceptanceContractError(
            "requirement %s source_preserved requires a frozen pre-execution content manifest"
            % requirement_id)
    bindings["source_manifest"] = manifest


def _seal_map_prestate(bindings: Dict[str, Any], context: Any, requirement_id: str) -> None:
    if context is None or not getattr(context, "view_state", None):
        raise AcceptanceContractError(
            "requirement %s lacks frozen pre-execution map snapshot" % requirement_id)
    bindings["pre_state"] = {
        "context_digest": getattr(context, "digest", None),
        "view_state": getattr(context, "view_state"),
        "active_data_frame": getattr(context, "active_data_frame", None),
        "layer_digest": digest([
            {"layer_ref": layer.identity.layer_ref,
             "name": layer.identity.name, "visible": layer.visible,
             "selection_count": layer.selection_count,
             "selection_hash": layer.selection_hash,
             "source_content_digest": layer.source_content_digest}
            for layer in getattr(context, "layers", ())
        ]),
    }
