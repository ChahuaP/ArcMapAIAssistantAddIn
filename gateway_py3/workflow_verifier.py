"""Closed deterministic verification of a prepared capability-contract workflow."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import ntpath
from typing import Any, Dict, Iterable
from arcmap_runtime_py2.capability_contract_protocol import (
    resolve_lowest_dimension_geometry,
    resolve_output_cardinality,
)

from .validators import ValidationError, prepare_workflow
from .semantic_domain import canonicalize_semantic_fact
from .plan_artifact import canonical_hash
from .artifact_identity import canonical_artifact_name


_GEOMETRY = {"point", "polyline", "polygon", "raster", "not_applicable"}
_KIND = {"feature_class", "raster", "table", "file", "map_state", "none"}
_FIELD_EFFECTS = {
    "not_applicable", "inherit_input", "inherit_tabular_fields", "inherit_target_merge_join", "merge_inputs",
    "aggregate_by_parameter_fields", "static_generated", "add_static_fields",
    "add_parameter_field", "delete_parameter_field", "in_place_update",
}


def _canonical_geometry(value):
    value = str(value or "").strip().lower()
    return value if value in _GEOMETRY else None


def _fields(value):
    if not isinstance(value, list):
        return None
    result = set()
    for field in value:
        name = field.get("name") if isinstance(field, dict) else field
        if not isinstance(name, str) or not name:
            return None
        result.add(name)
    return frozenset(result)


@dataclass(frozen=True)
class Fact:
    reference: str
    kind: str | None
    geometry: str | None
    fields: frozenset | None
    spatial_reference: str | None
    cardinality: str | None
    selection: str | None
    publication: str | None
    name: str | None
    format: str | None
    tabular_fields: frozenset | None = None
    destination: str | None = "not_applicable"

    def as_dict(self):
        return {
            "reference": self.reference, "kind": self.kind, "geometry": self.geometry,
            "fields": sorted(self.fields) if self.fields is not None else None,
            "spatial_reference": self.spatial_reference, "cardinality": self.cardinality,
            "selection": self.selection, "map_publication": self.publication,
            "name": self.name, "format": self.format,
            "tabular_fields": sorted(self.tabular_fields) if self.tabular_fields is not None else None,
            "destination": self.destination,
        }


class WorkflowVerifier:
    """Only known facts satisfy claims; unresolved facts become obligations."""

    def __init__(self, catalog):
        self.catalog = catalog

    def verify(self, workflow: Dict[str, Any], context: Dict[str, Any], task_contract: Dict[str, Any]):
        events = []
        blocking = [self._obligation("task.clarification", "clarifications", detail=item["question"])
                    for item in task_contract["clarifications"]]
        if blocking:
            report = self._report(None, [], [], blocking, [], events, [], [], set(), set())
            report["task_contract"] = deepcopy(task_contract)
            return report
        workflow = self._bind_declared_entities(workflow, task_contract)
        try:
            prepared = prepare_workflow(workflow, self.catalog, context, events)
        except ValidationError as exc:
            return self._report(None, [self._violation("workflow_structure", "workflow.invalid", message=str(exc))], [], [], [], events, [], [], set(), set())
        artifacts = self._context_facts(context)
        violations, obligations, facts, semantic_facts = [], [], [], []
        side_effects, authorization_scopes = set(), set()
        executed = []
        for step in prepared["steps"]:
            contract = self.catalog.capabilities.get(step["operation"])
            inputs, input_errors = self._inputs(step, contract, artifacts)
            violations.extend(input_errors)
            side_effects.add(contract["side_effects"])
            authorization_scopes.add((contract.get("authorization") or {"scope": "none"})["scope"])
            executed.append((step, contract))
            if input_errors:
                facts.append({"step_id": step["id"], "capability_id": step["operation"], "status": "dependency_contaminated"})
                continue
            output, update = self._output(step, contract, inputs, obligations)
            if update:
                artifacts[update.reference] = update
            if output:
                artifacts[output.reference] = output
            step_semantic_facts = self._semantic_facts(step, contract, output or update, inputs)
            semantic_facts.extend(step_semantic_facts)
            self._apply_map_selection_effects(artifacts, step_semantic_facts)
            facts.append({
                "step_id": step["id"], "capability_id": step["operation"], "inputs": [{"parameter": parameter, **item.as_dict()} for parameter, item in inputs],
                "output": output.as_dict() if output else None, "in_place_update": update.as_dict() if update else None,
            })
        semantic_facts = self._compose_semantic_facts(semantic_facts)
        for step, contract in executed:
            effect = contract["side_effects"]
            if effect == "read_only" or effect in task_contract["allowed_side_effects"]:
                continue
            if self._allows_owned_output_mutation(step, contract, task_contract):
                continue
            violations.append(self._violation("authorization.side_effect", "authorization.side_effect", step_id=step["id"], actual=effect))
        output_results = self._task_outputs(task_contract, artifacts.values(), violations, obligations)
        requirements = self._requirements(task_contract, artifacts.values(), semantic_facts, prepared, violations, obligations)
        obligations.extend(self._request_alignment_obligations(task_contract))
        report = self._report(prepared, violations, obligations, blocking, facts, events, output_results, requirements, side_effects, authorization_scopes)
        report["task_contract"] = deepcopy(task_contract)
        return report

    @staticmethod
    def _request_alignment_obligations(task_contract):
        """Expose the NL-to-contract boundary to G3 without pretending it is deterministic."""
        result = []
        for requirement in task_contract.get("requirements", []):
            predicate = requirement["predicate"]
            item = WorkflowVerifier._obligation(
                "request_alignment.unresolved", "requirements.predicate",
                requirement_id=requirement["requirement_id"],
                expected=requirement["evidence"], actual=predicate,
            )
            item["obligation_id"] += "|" + canonical_hash({
                "evidence": requirement["evidence"],
                "predicate": predicate,
            })
            result.append(item)
        for output in task_contract.get("outputs", []):
            destination = output.get("destination")
            if destination in {"default", "not_applicable"}:
                continue
            item = WorkflowVerifier._obligation(
                "request_alignment.output_destination", "outputs.destination",
                output_id=output["output_id"], expected=output["evidence"],
                actual=destination,
            )
            item["obligation_id"] += "|" + canonical_hash({
                "evidence": output["evidence"], "destination": destination,
            })
            result.append(item)
        return result

    @staticmethod
    def _allows_owned_output_mutation(step, contract, task_contract):
        """A write-authorized run may refine only its own earlier data products."""
        if contract["side_effects"] != "edits_data" or "writes_data" not in task_contract["allowed_side_effects"]:
            return False
        references = []
        for spec in contract["inputs"]:
            value = step["arguments"].get(spec["parameter"])
            references.extend(value if isinstance(value, list) else [value])
        return bool(references) and all(isinstance(value, str) and value.startswith("from_step:") for value in references)

    def _bind_declared_entities(self, workflow, task_contract):
        """Compile closed semantic input ids into their run-local ArcMap references.

        Entity ids belong to the task contract, while operation inputs belong to the
        ArcMap execution context.  This is an explicit compiler boundary: only an
        exact id declared by ``input_entities`` is rewritten; names, partial
        matches, and unknown values are left untouched for deterministic rejection.
        """
        bindings = {
            item["entity_id"]: item["reference"]
            for item in task_contract.get("input_entities", [])
        }
        if not bindings:
            return workflow
        compiled = deepcopy(workflow)
        for step in compiled.get("steps", []):
            contract = self.catalog.capabilities.get(step.get("operation"))
            if contract is None:
                continue
            arguments = step.get("arguments")
            if not isinstance(arguments, dict):
                continue
            for spec in contract["inputs"]:
                parameter = spec["parameter"]
                value = arguments.get(parameter)
                if isinstance(value, str) and value in bindings:
                    arguments[parameter] = bindings[value]
                elif isinstance(value, list):
                    arguments[parameter] = [bindings.get(item, item) for item in value]
        return compiled

    @staticmethod
    def _report(prepared, violations, obligations, blocking, facts, events, output_results, requirements, side_effects, authorization_scopes):
        return {
            "ok": not violations and not blocking,
            "hard_violations": violations,
            "review_obligations": obligations,
            "blocking_clarifications": blocking,
            "facts": facts,
            "normalization_events": events,
            "prepared_workflow": prepared,
            "output_results": output_results,
            "requirements": requirements,
            "side_effects": sorted(side_effects),
            "authorization_scopes": sorted(authorization_scopes),
        }

    @staticmethod
    def _violation(code, contract_path, step_id=None, output_id=None, requirement_id=None, actual=None, expected=None, message=None):
        identity = "|".join(str(value or "") for value in (code, requirement_id, output_id, step_id, contract_path))
        item = {"violation_id": identity, "code": code, "contract_path": contract_path}
        for key, value in (("step_id", step_id), ("output_id", output_id), ("requirement_id", requirement_id), ("actual", actual), ("expected", expected), ("message", message)):
            if value is not None:
                item[key] = value
        return item

    @staticmethod
    def _obligation(code, contract_path, requirement_id=None, output_id=None, step_id=None, capability_id=None, expected=None, actual=None, detail=None):
        item = {"obligation_id": "|".join(str(value or "") for value in (code, requirement_id, output_id, step_id, contract_path)), "code": code, "contract_path": contract_path}
        for key, value in (("requirement_id", requirement_id), ("output_id", output_id), ("step_id", step_id), ("capability_id", capability_id), ("expected", expected), ("actual", actual), ("detail", detail)):
            if value is not None:
                item[key] = value
        return item

    @staticmethod
    def _context_facts(context):
        result = {}
        for layer in context.get("layers", []) or []:
            geometry = _canonical_geometry(layer.get("geometry_type") or layer.get("shape_type"))
            kind = "raster" if geometry == "raster" else "feature_class"
            fields = _fields(layer.get("fields"))
            fact = Fact(
                reference=str(layer.get("layer_ref") or layer.get("name") or ""), kind=kind, geometry=geometry,
                fields=fields, spatial_reference=layer.get("spatial_reference") or layer.get("spatialReference"),
                cardinality="existing", selection="selected" if int(layer.get("selected_count") or 0) > 0 else "all",
                publication="published", name=layer.get("name"), format=None,
                tabular_fields=WorkflowVerifier._tabular_fields(layer.get("fields")),
            )
            for reference in (layer.get("layer_ref"), layer.get("name"), layer.get("longName")):
                if isinstance(reference, str) and reference:
                    result[reference] = fact
        return result

    def _inputs(self, step, contract, artifacts):
        resolved, violations = [], []
        for spec in contract["inputs"]:
            raw = step["arguments"].get(spec["parameter"])
            references = raw if isinstance(raw, list) else [raw]
            if raw is None:
                continue
            if spec["cardinality"] == "many" and not isinstance(raw, list):
                violations.append(self._violation("input.cardinality", "inputs.%s.cardinality" % spec["parameter"], step["id"], actual="one", expected="many"))
                continue
            for reference in references:
                fact = self._fact_for_reference(artifacts, reference)
                if fact is None:
                    violations.append(self._violation("dependency.unresolved", "inputs.%s" % spec["parameter"], step["id"], actual=reference))
                    continue
                if not any(self._kind_matches(expected, fact.kind) for expected in spec["data_kind"]):
                    violations.append(self._violation("input.kind", "inputs.%s.data_kind" % spec["parameter"], step["id"], actual=fact.kind, expected=spec["data_kind"]))
                if fact.geometry not in spec["geometry"]:
                    violations.append(self._violation("input.geometry", "inputs.%s.geometry" % spec["parameter"], step["id"], actual=fact.geometry, expected=spec["geometry"]))
                if fact.fields is None:
                    violations.append(self._violation("input.fields_unresolved", "inputs.%s.required_fields" % spec["parameter"], step["id"]))
                elif not set(spec["required_fields"]).issubset(fact.fields):
                    violations.append(self._violation("input.fields", "inputs.%s.required_fields" % spec["parameter"], step["id"], actual=sorted(fact.fields), expected=spec["required_fields"]))
                if self._requires_live_selection(spec["selection"], step, contract) and fact.selection != "selected":
                    violations.append(self._violation(
                        "input.selection_required", "inputs.%s.selection" % spec["parameter"],
                        step["id"], actual=fact.selection, expected="selected",
                    ))
                resolved.append((spec["parameter"], fact))
        return resolved, violations

    @staticmethod
    def _apply_map_selection_effects(artifacts, semantic_facts):
        for semantic_fact in semantic_facts:
            kind = semantic_fact.get("kind")
            if kind in {"attribute_filter", "spatial_filter"}:
                selection = "selected"
            elif kind == "map_change" and semantic_fact.get("action") == "clear_selection":
                selection = "all"
            else:
                continue
            reference = semantic_fact.get("target")
            fact = WorkflowVerifier._fact_for_reference(artifacts, reference)
            if fact is None:
                continue
            updated = replace(fact, selection=selection)
            for alias, candidate in list(artifacts.items()):
                if candidate is fact or candidate.reference == fact.reference:
                    artifacts[alias] = updated

    @staticmethod
    def _requires_live_selection(requirement, step, contract):
        rule = requirement["rule"]
        if rule == "requires_selected":
            return True
        if rule == "any":
            return False
        if rule == "parameter_values_require_selected":
            parameter = requirement["parameter"]
            value = step["arguments"].get(parameter)
            if value is None:
                value = contract["parameters_schema"]["properties"][parameter].get("default")
            return value in requirement["values"]
        raise RuntimeError("unhandled input selection rule: %s" % rule)

    @staticmethod
    def _fact_for_reference(artifacts, reference):
        """Resolve an exact stable reference regardless of its presentation alias."""
        if not isinstance(reference, str):
            return None
        direct = artifacts.get(reference)
        if direct is not None:
            return direct
        return next((fact for fact in artifacts.values() if fact.reference == reference), None)

    def _output(self, step, contract, inputs, obligations):
        output = contract["outputs"]
        effect = output["fields"]["effect"]
        if effect not in _FIELD_EFFECTS:
            raise RuntimeError("unregistered field effect: %s" % effect)
        by_parameter = {}
        for parameter, fact in inputs:
            by_parameter.setdefault(parameter, []).append(fact)
        field_descriptor = output["fields"]
        field_parameters = field_descriptor.get("sources") or [field_descriptor["target"]]
        target_inputs = [
            fact for parameter in field_parameters for fact in by_parameter.get(parameter, [])
        ]
        fields = self._effect_fields(
            effect, target_inputs, field_descriptor, step,
            contract["parameters_schema"], obligations,
        )
        tabular_inputs = [(parameter, replace(fact, fields=fact.tabular_fields)) for parameter, fact in inputs]
        tabular_by_parameter = {}
        for parameter, fact in tabular_inputs:
            tabular_by_parameter.setdefault(parameter, []).append(fact)
        tabular_targets = [
            fact for parameter in field_parameters
            for fact in tabular_by_parameter.get(parameter, [])
        ]
        tabular_fields = self._effect_fields(
            effect, tabular_targets, field_descriptor, step,
            contract["parameters_schema"], [],
        )
        geometry_descriptor = output["geometry"]
        geometry_inputs = by_parameter.get(geometry_descriptor["value"], [])
        spatial_descriptor = output["spatial_reference"]
        spatial_inputs = by_parameter.get(spatial_descriptor["input"], [])
        geometry = self._geometry(geometry_descriptor, geometry_inputs, step)
        spatial = self._spatial(spatial_descriptor, spatial_inputs, step, obligations)
        name = step["arguments"].get("output_name") if isinstance(step["arguments"].get("output_name"), str) else None
        fmt = self._format(output["format"], step)
        artifact_kind = output["kind"] if output["kind"] != "none" else "map_state"
        cardinality = resolve_output_cardinality(
            output["cardinality"], step["arguments"], contract["parameters_schema"],
            RuntimeError, "outputs.cardinality",
        )
        selection = self._resolved_output_selection(output["selection_state"], target_inputs)
        artifact = Fact(
            reference="from_step:" + step["id"], kind=artifact_kind, geometry=geometry, fields=fields,
            spatial_reference=spatial, cardinality=cardinality, selection=selection,
            publication=output["map_publication"], name=name, format=fmt, tabular_fields=tabular_fields,
            destination=self._destination(contract, step),
        )
        update = None
        if effect in {"add_static_fields", "add_parameter_field", "delete_parameter_field", "in_place_update"} and target_inputs:
            update = replace(target_inputs[0], fields=fields, tabular_fields=tabular_fields,
                             spatial_reference=(target_inputs[0].spatial_reference
                                                if spatial in (None, "not_applicable") else spatial),
                             selection=selection)
        # A mutating/no-file operation still has a named semantic subject.
        # Dropping it made correct in-place and map claims unprovable.
        return artifact, update

    @staticmethod
    def _resolved_output_selection(selection_state, target_inputs):
        if selection_state == "selection_preserved":
            return target_inputs[0].selection if target_inputs else None
        if selection_state == "applied":
            return "selected"
        if selection_state == "cleared":
            return "all"
        if selection_state == "not_applicable":
            return "not_applicable"
        raise RuntimeError("unhandled output selection state: %s" % selection_state)

    @staticmethod
    def _format(descriptor, step):
        rule = descriptor["rule"]
        if rule == "not_applicable":
            return "not_applicable"
        if rule == "fixed":
            return descriptor["value"]
        if rule == "from_parameter":
            return step["arguments"].get(descriptor["parameter"], descriptor["default"])
        raise RuntimeError("unhandled output format rule: %s" % rule)

    @staticmethod
    def _destination(contract, step):
        if contract["side_effects"] != "writes_data":
            return "not_applicable"
        arguments = step["arguments"]
        return arguments.get("output_folder") or arguments.get("output_workspace") or "default"

    @staticmethod
    def _semantic_facts(step, contract, artifact, inputs):
        """Bind only declared contract slots; never infer meaning from operation ids."""
        result = []
        canonical_inputs = {name: [fact.reference for parameter, fact in inputs if parameter == name] for name in {parameter for parameter, _ in inputs}}
        schema = contract.get("parameters_schema", {}).get("properties", {})
        def resolve(binding):
            if isinstance(binding, list): return [resolve(item) for item in binding]
            if not isinstance(binding, dict) or len(binding) != 1: raise RuntimeError("invalid semantic binding")
            key, value = next(iter(binding.items()))
            if key == "output": return artifact.reference if artifact else None
            if key == "const": return value
            if key == "parameter":
                values = canonical_inputs.get(value)
                if values: return values if len(values) > 1 else values[0]
                if value in step["arguments"]: return step["arguments"][value]
                return schema.get(value, {}).get("default")
            raise RuntimeError("unknown semantic binding")
        def flatten_entities(value):
            if not isinstance(value, list):
                return [value]
            flattened = []
            for item in value:
                flattened.extend(flatten_entities(item))
            return flattened
        for effect in contract["semantic_effects"]:
            fact = {"kind": effect["kind"], "subject": artifact.reference if artifact is not None else None, "step_id": step["id"]}
            for name, binding in effect.items():
                if name in {"kind", "result", "preserves"}: continue
                resolved = resolve(binding)
                fact[name] = flatten_entities(resolved) if name == "sources" else resolved
            fact = canonicalize_semantic_fact(fact, "steps.%s.semantic_effect" % step["id"], ValueError)
            if effect.get("preserves"):
                fact["_preserves"] = tuple(effect["preserves"])
            result.append(fact)
        return result

    @staticmethod
    def _compose_semantic_facts(semantic_facts):
        """Compose semantic proofs only across catalog-declared lineage edges.

        Exporting every row of an artifact created by ``export_selected_features``
        is exactly the same row set as exporting the source selection.  The proof
        is valid only across the explicit ``from_step`` edge and the predecessor's
        closed ``selected_only=True`` semantic fact.  Other transformations may
        propagate named upstream semantic kinds only when their capability effect
        explicitly declares ``preserves`` and binds a single ``source`` edge.
        """
        result = list(semantic_facts)
        selected_exports = {
            fact["subject"]: fact
            for fact in semantic_facts
            if fact.get("kind") == "artifact_export"
            and fact.get("action") == "export_selected_features"
            and fact.get("selected_only") is True
            and isinstance(fact.get("subject"), str)
            and fact["subject"].startswith("from_step:")
        }
        for fact in semantic_facts:
            if (fact.get("kind") != "artifact_export"
                    or fact.get("action") != "table_csv"
                    or fact.get("selected_only") is not False):
                continue
            predecessor = selected_exports.get(fact.get("target"))
            if predecessor is None:
                continue
            derived = dict(fact)
            derived["target"] = predecessor["target"]
            derived["selected_only"] = True
            result.append(derived)
        transformers_by_source = {}
        for transformer in result:
            source = transformer.get("source")
            subject = transformer.get("subject")
            if (transformer.get("_preserves") and isinstance(source, str)
                    and isinstance(subject, str)):
                transformers_by_source.setdefault(source, []).append(transformer)
        pending = list(result)
        seen = set()
        cursor = 0
        while cursor < len(pending):
            upstream = pending[cursor]
            cursor += 1
            for transformer in transformers_by_source.get(upstream.get("subject"), ()):
                if upstream.get("kind") not in transformer["_preserves"]:
                    continue
                derived = {
                    key: value for key, value in upstream.items()
                    if key not in {"_preserves", "_lineage_steps", "_identity"}
                }
                derived["subject"] = transformer["subject"]
                derived["_lineage_steps"] = tuple(upstream.get("_lineage_steps", ())) + (
                    transformer["step_id"],
                )
                identity = canonical_hash({
                    "fact": {key: value for key, value in derived.items() if not key.startswith("_")},
                    "lineage_steps": derived["_lineage_steps"],
                })
                if identity in seen:
                    continue
                seen.add(identity)
                derived["_identity"] = identity
                result.append(derived)
                pending.append(derived)
        return result

    @staticmethod
    def _effect_fields(effect, target_inputs, descriptor, step, parameters_schema, obligations):
        target_fields = target_inputs[0].fields if target_inputs else frozenset()
        all_fields = [fact.fields for fact in target_inputs]
        if effect == "not_applicable": return frozenset()
        if effect == "inherit_tabular_fields":
            return target_inputs[0].tabular_fields if target_inputs else frozenset()
        if effect in {"inherit_input", "inherit_target_merge_join", "in_place_update", "add_static_fields", "add_parameter_field", "delete_parameter_field"}:
            fields = target_fields
        elif effect == "merge_inputs":
            fields = frozenset().union(*(value or frozenset() for value in all_fields))
        elif effect == "aggregate_by_parameter_fields":
            parameter_name = "dissolve_fields" if "dissolve_fields" in parameters_schema.get("properties", {}) else "fields"
            parameters = step["arguments"].get(parameter_name)
            if parameters is None:
                parameters = parameters_schema.get("properties", {}).get(parameter_name, {}).get("default")
            if not isinstance(parameters, list):
                obligations.append(WorkflowVerifier._obligation("aggregate.grain_unresolved", "outputs.fields", step_id=step["id"]))
                return None
            fields = frozenset(str(value).lstrip("#") for value in parameters)
        elif effect == "static_generated": fields = frozenset()
        else: raise RuntimeError("unhandled field effect: %s" % effect)
        fields = (fields or frozenset()) | frozenset(descriptor["static_fields"])
        parameter = descriptor["parameter_field"]
        if effect == "add_parameter_field":
            value = step["arguments"].get(parameter)
            return None if not isinstance(value, str) or not value else fields | {value.lstrip("#")}
        if effect == "delete_parameter_field":
            value = step["arguments"].get(parameter)
            return None if not isinstance(value, str) or not value else fields - {value.lstrip("#")}
        return fields

    @staticmethod
    def _tabular_fields(fields):
        if not isinstance(fields, list):
            return None
        excluded = {"geometry", "raster", "blob"}
        result = set()
        for field in fields:
            if isinstance(field, dict):
                name = field.get("name")
                field_type = str(field.get("type") or "").lower()
            else:
                name, field_type = field, ""
            if not isinstance(name, str) or not name:
                return None
            if field_type not in excluded:
                result.add(name)
        return frozenset(result)

    @staticmethod
    def _geometry(descriptor, target_inputs, step):
        if descriptor["rule"] == "not_applicable": return "not_applicable"
        if descriptor["rule"] == "inherit": return target_inputs[0].geometry if target_inputs else None
        if descriptor["rule"] == "lowest_dimension":
            geometries = [item.geometry for item in target_inputs]
            if not geometries or any(value is None for value in geometries):
                return None
            return resolve_lowest_dimension_geometry(geometries, RuntimeError, "outputs.geometry")
        value = descriptor["value"]
        return _canonical_geometry(step["arguments"].get(value)) if value == "parameter_geometry_type" else _canonical_geometry(value)

    @staticmethod
    def _spatial(descriptor, target_inputs, step, obligations):
        if descriptor["rule"] == "not_applicable": return "not_applicable"
        if descriptor["rule"] == "inherit": return target_inputs[0].spatial_reference if target_inputs else None
        if descriptor["rule"] == "from_parameter":
            value = step["arguments"].get(descriptor["input"])
            if isinstance(value, int) and not isinstance(value, bool): return "EPSG:%d" % value
            if isinstance(value, str) and value.isdigit(): return "EPSG:" + value
            return value if isinstance(value, str) and value else None
        value = step["arguments"].get(descriptor["input"])
        if descriptor["rule"] == "from_parameter_or_map" and not value: value = step["arguments"].get("spatial_reference")
        if not isinstance(value, str) or not value:
            obligations.append(WorkflowVerifier._obligation("spatial_reference.unresolved", "outputs.spatial_reference", step_id=step["id"]))
            return None
        return value

    def _task_outputs(self, task, artifacts: Iterable[Fact], violations, obligations):
        produced = list(artifacts)
        results = []
        for output in task["outputs"]:
            before = len(violations) + len(obligations)
            candidates = [fact for fact in produced if self._matches_output(output, fact)]
            if not candidates:
                violations.append(self._violation("output.missing", "outputs", output_id=output["output_id"], expected=output["name"]))
                results.append({"output_id": output["output_id"], "satisfied": False})
                continue
            fact = candidates[-1]
            self._compare_output(output, fact, violations, obligations)
            results.append({"output_id": output["output_id"], "satisfied": before == len(violations) + len(obligations)})
        return results

    @staticmethod
    def _kind_matches(expected, actual):
        return expected == actual or (expected == "feature_layer" and actual == "feature_class") or (expected == "raster_layer" and actual == "raster")

    @classmethod
    def _matches_output(cls, output, fact):
        if not cls._kind_matches(output["kind"], fact.kind):
            return False
        if output["kind"] == "map_state" and fact.kind == "map_state":
            return True
        return canonical_artifact_name(output["name"], output["format"]) == canonical_artifact_name(fact.name, fact.format)

    def _compare_output(self, expected, fact, violations, obligations):
        for key, actual, wanted in (("geometry", fact.geometry, expected["geometry"]), ("spatial_reference", fact.spatial_reference, expected["spatial_reference"]), ("format", fact.format, expected["format"])):
            if wanted in ("", "not_applicable") and key != "geometry": continue
            if actual is None: obligations.append(self._obligation("output.%s_unresolved" % key, "outputs.%s" % key, output_id=expected["output_id"]))
            elif actual != wanted:
                violations.append(self._violation("output.%s" % key, "outputs.%s" % key, output_id=expected["output_id"], actual=actual, expected=wanted))
        if fact.fields is None: obligations.append(self._obligation("output.fields_unresolved", "outputs.required_fields", output_id=expected["output_id"]))
        elif not set(expected["required_fields"]).issubset(fact.fields): violations.append(self._violation("output.fields", "outputs.required_fields", output_id=expected["output_id"], actual=sorted(fact.fields), expected=expected["required_fields"]))
        if self._canonical_destination(fact.destination) != self._canonical_destination(expected["destination"]):
            violations.append(self._violation(
                "output.destination", "outputs.destination", output_id=expected["output_id"],
                actual=fact.destination, expected=expected["destination"],
            ))

    @staticmethod
    def _canonical_destination(value):
        text = str(value or "").strip()
        if text in {"default", "not_applicable"}:
            return text
        return ntpath.normcase(ntpath.normpath(text))

    @staticmethod
    def _selector_selection_invalidator(selector_reference, selection_proof, consumer_fact,
                                          semantic_facts, step_order):
        """Return the first workflow fact that makes a selector proof non-live."""
        producer_step_id = selection_proof["step_id"]
        consumer_step_id = consumer_fact["step_id"]
        producer_index = step_order[producer_step_id]
        consumer_index = step_order[consumer_step_id]
        if producer_index >= consumer_index:
            return {
                "step_id": consumer_step_id,
                "kind": "workflow_order",
                "action": "producer_not_before_consumer",
            }
        for fact in semantic_facts:
            fact_step_id = fact.get("step_id")
            if fact_step_id not in step_order:
                continue
            fact_index = step_order[fact_step_id]
            if not producer_index < fact_index < consumer_index:
                continue
            if fact.get("target") != selector_reference:
                continue
            if fact.get("kind") in {"attribute_filter", "spatial_filter"}:
                return fact
            if fact.get("kind") == "map_change" and fact.get("action") == "clear_selection":
                return fact
        return None

    def _requirements(self, task, artifacts, semantic_facts, prepared, violations, obligations):
        outputs = {output["output_id"]: output for output in task["outputs"]}
        inputs = {item["entity_id"]: item for item in task.get("input_entities", [])}
        produced = list(artifacts)
        entity_references = {}
        for entity in task.get("input_entities", []):
            fact = next((item for item in produced if item.reference == entity["reference"]), None)
            if fact is None:
                fact = next((item for item in produced if item.name == entity["reference"]), None)
            if fact is not None:
                entity_references[entity["entity_id"]] = fact.reference
        for output_id, output in outputs.items():
            fact = next((item for item in produced if self._matches_output(output, item)), None)
            if fact is not None:
                entity_references[output_id] = fact.reference
        results = []
        selection_proofs = {}
        step_order = {step["id"]: index for index, step in enumerate(prepared["steps"])}
        for requirement in task["requirements"]:
            predicate = requirement["predicate"]
            subject = outputs.get(predicate["subject"])
            input_subject = inputs.get(predicate["subject"])
            if subject is None and input_subject is None:
                obligations.append(self._obligation("requirement.subject_unresolved", "requirements.predicate.subject", requirement_id=requirement["requirement_id"], detail=predicate["subject"]))
                results.append({"requirement_id": requirement["requirement_id"], "satisfied": False})
                continue
            candidates = ([fact for fact in produced if self._matches_output(subject, fact)]
                          if subject is not None else
                          [fact for fact in produced if fact.reference == input_subject["reference"]])
            if not candidates:
                obligations.append(self._obligation("requirement.subject_unresolved", "requirements.predicate.subject", requirement_id=requirement["requirement_id"], output_id=subject["output_id"] if subject is not None else None))
                results.append({"requirement_id": requirement["requirement_id"], "satisfied": False})
                continue
            if predicate["kind"] == "source_preserved":
                reference = entity_references.get(predicate["subject"])
                edited = []
                for step in prepared["steps"]:
                    contract = self.catalog.capabilities.get(step["operation"])
                    if contract["side_effects"] != "edits_data":
                        continue
                    values = []
                    for argument in step["arguments"].values():
                        values.extend(argument if isinstance(argument, list) else [argument])
                    if reference in values:
                        edited.append(step["id"])
                if edited:
                    violations.append(self._violation("requirement.source_modified", "requirements.predicate", requirement_id=requirement["requirement_id"], actual=edited, expected="no edits_data operation may target the declared source"))
                    results.append({"requirement_id": requirement["requirement_id"], "satisfied": False})
                else:
                    results.append({"requirement_id": requirement["requirement_id"], "satisfied": True, "proof": {"source_reference": reference, "edited_steps": []}})
                continue
            fact = candidates[-1]
            candidates = [item for item in semantic_facts if item["subject"] == fact.reference and item["kind"] == predicate["kind"]]
            if input_subject is not None and not candidates and predicate["kind"] in {"attribute_filter", "spatial_filter"}:
                candidates = [item for item in semantic_facts if item.get("target") == fact.reference and item["kind"] == predicate["kind"]]
            expected = {key: value for key, value in predicate.items() if key not in {"kind", "subject"}}
            for key, value in list(expected.items()):
                if isinstance(value, str): expected[key] = entity_references.get(value, value)
                elif isinstance(value, list): expected[key] = [entity_references.get(item, item) for item in value]
            exact = [item for item in candidates if all(item.get(key) == value for key, value in expected.items())]
            if exact:
                semantic_fact = {key: value for key, value in exact[-1].items() if not key.startswith("_")}
                proof = {"step_id": exact[-1]["step_id"], "semantic_fact": semantic_fact}
                if exact[-1].get("_lineage_steps"):
                    proof["lineage_steps"] = list(exact[-1]["_lineage_steps"])
                if predicate["kind"] == "spatial_filter":
                    selector_reference = entity_references.get(predicate["selector"], predicate["selector"])
                    selector_selection = selection_proofs.get(selector_reference)
                    if selector_selection is not None:
                        invalidator = self._selector_selection_invalidator(
                            selector_reference, selector_selection, exact[-1], semantic_facts,
                            step_order,
                        )
                        if invalidator is not None:
                            invalidator_actual = {"invalidator_kind": invalidator["kind"]}
                            if invalidator.get("action") is not None:
                                invalidator_actual["invalidator_action"] = invalidator["action"]
                            violations.append(self._violation(
                                "requirement.selector_selection_invalidated",
                                "requirements.predicate.selector",
                                step_id=invalidator["step_id"],
                                requirement_id=requirement["requirement_id"],
                                actual=invalidator_actual,
                                expected={
                                    "selector_reference": selector_reference,
                                    "producer_step_id": selector_selection["step_id"],
                                },
                            ))
                            results.append({
                                "requirement_id": requirement["requirement_id"],
                                "satisfied": False,
                            })
                            continue
                        proof["selector_selection"] = selector_selection
                results.append({"requirement_id": requirement["requirement_id"], "satisfied": True, "proof": proof})
                if predicate["kind"] in {"attribute_filter", "spatial_filter"}:
                    target_id = predicate.get("target", predicate["subject"])
                    target_reference = entity_references.get(target_id, target_id)
                    selection_proofs[target_reference] = {
                        "requirement_id": requirement["requirement_id"],
                        "step_id": exact[-1]["step_id"],
                        "semantic_fact": semantic_fact,
                    }
            elif candidates:
                violations.append(self._violation("requirement.semantic_conflict", "requirements.predicate", requirement_id=requirement["requirement_id"], output_id=subject["output_id"] if subject is not None else None, actual={key: candidates[-1].get(key) for key in expected}, expected=expected))
                results.append({"requirement_id": requirement["requirement_id"], "satisfied": False})
            else:
                obligations.append(self._obligation("requirement.semantic_unresolved", "requirements.predicate", requirement_id=requirement["requirement_id"], output_id=subject["output_id"] if subject is not None else None, detail=predicate["kind"]))
                results.append({"requirement_id": requirement["requirement_id"], "satisfied": False})
        return results
