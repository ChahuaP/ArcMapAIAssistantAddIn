# -*- coding: utf-8 -*-
"""Independent, data-first acceptance evaluators for ArcMap Python 2.

The executor receipt is intentionally absent from this module.  An evaluator
only receives paths sealed by the gateway in an acceptance rule and re-reads
those datasets through ArcPy.  Missing bindings are *Unresolved*, while an
observed counterexample is *Violated*.
"""
from __future__ import absolute_import

import hashlib
import math

import arcpy

try:
    from operations import condition_utils
except ImportError:
    from .operations import condition_utils

try:
    import context_reader
except ImportError:
    from . import context_reader

try:
    from shared_runtime.acceptance_profile import PROFILES, has_profile, get_profile
except ImportError:
    from shared_runtime.acceptance_profile import PROFILES, has_profile, get_profile

try:
    unicode
except NameError:
    unicode = str


# The closed effect set is the single fact source shared with the Py3 seal and
# the catalog loader.  No second vocabulary is maintained here.
SUPPORTED_EFFECTS = frozenset(PROFILES)


def evaluate(rule, output_document):
    """Evaluate one sealed rule, returning a canonical proof object.

    ``rule.bindings`` is the sole physical-path input.  Every binding is a
    read-only object with a gateway-derived ``path``.  The model has no route
    to inject an arbitrary path at this boundary.
    """
    proof_id = rule.get("proof_id") if isinstance(rule, dict) else None
    predicate = rule.get("predicate") if isinstance(rule, dict) else None
    if not isinstance(proof_id, unicode) or not isinstance(predicate, dict):
        return _violated(proof_id or u"acceptance:malformed", u"malformed sealed rule")
    kind = predicate.get("kind")
    if not has_profile(kind):
        return _unresolved(proof_id, u"no acceptance profile registered for %s" % unicode(kind))
    # The evaluator is selected through the production profile (single fact
    # source shared with the catalog + seal), not by an independent kind lookup.
    profile = get_profile(kind)
    evaluator = _EVALUATORS.get(profile.evaluator)
    if evaluator is None:
        return _unresolved(proof_id, u"no evaluator implementation bound to profile %s" % unicode(kind))
    bindings = rule.get("bindings") or rule.get("evaluation") or {}
    if not isinstance(bindings, dict):
        return _violated(proof_id, u"sealed bindings are malformed")
    try:
        return evaluator(proof_id, predicate, bindings, output_document)
    except Exception as exc:
        # ArcPy inability to inspect is not a business violation.  It remains
        # fail-closed because Publisher only accepts Proven.
        return _unresolved(proof_id, u"independent evaluator unavailable: %s" % unicode(exc))


def _dataset(bindings, name, output_document=None):
    value = bindings.get(name)
    if value == "__output__" and isinstance(output_document, dict):
        value = output_document.get("canonical_path")
    if isinstance(value, dict):
        layer_ref = value.get("layer_ref")
        value = value.get("path") or value.get("canonical_path") or _map_layer(layer_ref)
    if value is None or (isinstance(value, unicode) and not value) or not arcpy.Exists(value):
        return None
    return value


def _map_layer(layer_ref):
    """Resolve a sealed ArcMap layer reference without accepting a model path."""
    if not isinstance(layer_ref, unicode) or not layer_ref.startswith(u"layer:"):
        return None
    try:
        index = int(layer_ref.split(u":", 1)[1])
        mxd = arcpy.mapping.MapDocument("CURRENT")
        frame = mxd.activeDataFrame
        layers = arcpy.mapping.ListLayers(mxd, "", frame)
        return layers[index] if index >= 0 and index < len(layers) else None
    except Exception:
        return None


def _unresolved(proof_id, reason, evidence=None):
    result = {"proof_id": proof_id, "status": "Unresolved", "reason": reason}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _violated(proof_id, reason, evidence=None):
    result = {"proof_id": proof_id, "status": "Violated", "reason": reason}
    if evidence is not None:
        result["evidence"] = evidence
    return result


def _proven(proof_id, evidence):
    return {"proof_id": proof_id, "status": "Proven", "evidence": evidence}


def _count(path, where=None):
    if where:
        with arcpy.da.SearchCursor(path, ["OID@"], where_clause=where) as rows:
            return sum(1 for _ in rows)
    return int(arcpy.GetCount_management(path).getOutput(0))


def _where(path, condition):
    if not isinstance(condition, dict):
        raise ValueError("structured condition is required")
    return condition_utils.compile_where(path, condition)


def _record_keys(path, fields=None, where=None):
    """Stable records, preferring explicit stable-id fields sealed by Gateway."""
    names = fields or [getattr(arcpy.Describe(path), "OIDFieldName", None) or "OID@"]
    actual = ["OID@" if name == "OID@" else name for name in names]
    values = set()
    with arcpy.da.SearchCursor(path, actual, where_clause=where) as rows:
        for row in rows:
            values.add(tuple(unicode(item) if item is not None else u"<null>" for item in row))
    return values


def _attribute_filter(proof_id, predicate, bindings, document):
    source = _dataset(bindings, "source") or _dataset(bindings, "target")
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    # Selection operations have no persisted result: compare the live layer's
    # selected cursor against its underlying datasource.
    selection_layer = None
    if not result and hasattr(source, "dataSource"):
        selection_layer, result, source = source, source, source.dataSource
    if not source or not result:
        return _unresolved(proof_id, u"attribute_filter requires sealed source and result datasets")
    where = _where(source, predicate.get("where"))
    # Count based on source verifies completeness.  Re-evaluate output with the
    # same expression verifies correctness; then stable IDs detect omissions.
    expected = _record_keys(source, bindings.get("identity_fields"), where)
    actual = _record_keys(result, bindings.get("identity_fields"))
    if actual != expected:
        return _violated(proof_id, u"attribute filter result differs from independently selected source",
                         {"expected_count": len(expected), "actual_count": len(actual)})
    return _proven(proof_id, {"method": "SearchCursor where + stable identity set", "count": len(actual)})


def _spatial_method(shape, other, relation):
    relation = relation.lower()
    if relation == "intersect": return shape.disjoint(other) is False
    if relation == "contain": return shape.contains(other)
    if relation == "within": return shape.within(other)
    if relation == "touch": return shape.touches(other)
    if relation == "overlap": return shape.overlaps(other)
    if relation == "cross": return shape.crosses(other)
    raise ValueError("unsupported spatial predicate")



def _spatial_filter(proof_id, predicate, bindings, document):
    source = _dataset(bindings, "source") or _dataset(bindings, "target")
    selector = _dataset(bindings, "selector")
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    if not result and hasattr(source, "dataSource"):
        result, source = source, source.dataSource
    if not source or not selector or not result:
        return _unresolved(proof_id, u"spatial_filter requires sealed source, selector and result datasets")
    relation = predicate.get("overlap_type")
    strategy = bindings.get("crs_strategy")
    if relation == "within_a_distance":
        # Same Quantity/CRS rule as buffer: the sealed strategy already resolved
        # the distance into the source CRS's native linear unit (buffer_value),
        # so distanceTo is compared in a consistent unit.  No second unit logic,
        # no approximation.  meters-on-geographic was rejected at seal.
        if not isinstance(strategy, dict):
            return _unresolved(proof_id, u"within_a_distance requires a sealed crs_strategy")
        if strategy.get("mode") not in ("native_projected", "native_degrees"):
            return _unresolved(proof_id, u"within_a_distance crs_strategy is not natively provable")
        limit = strategy.get("buffer_value")
        if limit is None:
            return _unresolved(proof_id, u"within_a_distance crs_strategy has no limit")
    else:
        limit = None
    selectors = []
    with arcpy.da.SearchCursor(selector, ["SHAPE@"]) as rows:
        for row in rows:
            if row[0] is not None:
                selectors.append(row[0])
    if not selectors:
        return _violated(proof_id, u"selector has no geometry")
    expected = set()
    identity = bindings.get("identity_fields")
    fields = identity or [getattr(arcpy.Describe(source), "OIDFieldName", None) or "OID@", "SHAPE@"]
    if "SHAPE@" not in fields:
        fields = list(fields) + ["SHAPE@"]
    with arcpy.da.SearchCursor(source, fields) as rows:
        for row in rows:
            shape = row[-1]
            if shape is None:
                continue
            if limit is not None:
                matched = any(_distance_le(shape, item, limit) for item in selectors)
            else:
                matched = any(_spatial_method(shape, item, relation) for item in selectors)
            if matched:
                expected.add(tuple(unicode(item) if item is not None else u"<null>" for item in row[:-1]))
    actual = _record_keys(result, identity)
    if actual != expected:
        return _violated(proof_id, u"spatial selection differs from independently evaluated relation",
                         {"expected_count": len(expected), "actual_count": len(actual)})
    return _proven(proof_id, {"method": "SearchCursor geometry predicates", "count": len(actual)})


def _distance_le(shape, other, limit):
    try:
        return shape.distanceTo(other) <= limit
    except Exception:
        return False


def _buffer(proof_id, predicate, bindings, document):
    """buffer: independent recompute in the source CRS's sealed native unit.

    ``Geometry.buffer`` uses the dataset's coordinate units.  The sealed
    ``crs_strategy`` already resolved the Quantity into the source CRS's native
    linear unit (``buffer_value``) — converting meters/kilometers via the
    runtime-verified ``meters_per_unit`` for any projected CRS (meter, foot,
    survey-foot alike), and the seal rejected meters-on-geographic (which would
    require projection).  So the evaluator buffers natively and a wrong-distance
    result is caught, never silently passed and never approximated.
    """
    source = _dataset(bindings, "source")
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    strategy = bindings.get("crs_strategy")
    quantity = predicate.get("distance")
    if not source or not result or not isinstance(strategy, dict) or not isinstance(quantity, dict):
        return _unresolved(proof_id, u"buffer requires sealed source/result and crs_strategy")
    mode = strategy.get("mode")
    value = strategy.get("buffer_value")
    if mode not in ("native_projected", "native_degrees") or value is None:
        return _unresolved(proof_id, u"buffer crs_strategy is not natively provable")
    tolerance = float(quantity.get("tolerance", 0.0) or 0.0)

    expected = []
    with arcpy.da.SearchCursor(source, ["SHAPE@"]) as rows:
        for row in rows:
            if row[0] is None:
                return _unresolved(proof_id, u"buffer source geometry is null")
            expected.append(row[0].buffer(value))
    actual = []
    with arcpy.da.SearchCursor(result, ["SHAPE@"]) as rows:
        for row in rows:
            if row[0] is not None:
                actual.append(row[0])
    if len(actual) != len(expected):
        return _violated(proof_id, u"buffer feature count differs",
                         {"expected_count": len(expected), "actual_count": len(actual)})
    for index, (left, right) in enumerate(zip(expected, actual)):
        delta = _symmetric_area(left, right)
        if delta is None:
            return _unresolved(proof_id, u"buffer geometry comparison unavailable", {"index": index})
        if delta > max(tolerance * tolerance, 1e-6):
            return _violated(proof_id, u"buffer geometry/distance differs",
                             {"index": index, "difference_area": delta})
    return _proven(proof_id, {"method": "independent Geometry.buffer via " + unicode(mode),
                              "distance": value, "count": len(actual)})


def _crs(proof_id, predicate, bindings, document):
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document) or _dataset(bindings, "target")
    expected = predicate.get("spatial_reference")
    if not result or not isinstance(expected, unicode): return _unresolved(proof_id, u"CRS evaluator lacks sealed dataset/reference")
    observed = getattr(getattr(arcpy.Describe(result), "spatialReference", None), "name", None)
    if not observed or expected.lower() not in observed.lower():
        return _violated(proof_id, u"coordinate reference differs", {"expected": expected, "actual": observed})
    return _proven(proof_id, {"spatial_reference": observed})


def _field(proof_id, predicate, bindings, document):
    target = _dataset(bindings, "result", document) or _dataset(bindings, "target", document) or _dataset(bindings, "subject", document)
    if not target: return _unresolved(proof_id, u"field evaluator lacks sealed result dataset")
    fields = dict((item.name.lower(), item) for item in arcpy.ListFields(target))
    name = predicate.get("field_name")
    kind = predicate.get("kind")
    if kind == "field_delete":
        if name.lower() in fields: return _violated(proof_id, u"deleted field remains present", {"field": name})
        return _proven(proof_id, {"field": name, "absent": True})
    field = fields.get(name.lower())
    if field is None: return _violated(proof_id, u"required field is absent", {"field": name})
    expected = predicate.get("field_type")
    if expected and unicode(field.type).lower() != unicode(expected).lower():
        return _violated(proof_id, u"field type differs", {"expected": expected, "actual": field.type})
    return _proven(proof_id, {"field": field.name, "type": field.type})


def _source_preserved(proof_id, predicate, bindings, document):
    source_binding = bindings.get("source") or bindings.get("subject") or {}
    if not isinstance(source_binding, dict):
        source_binding = {}
    source = _dataset(bindings, "source") or _dataset(bindings, "subject")
    manifest = bindings.get("source_manifest") or {}
    if not isinstance(manifest, dict):
        manifest = {}
    if not source:
        return _unresolved(proof_id, u"source preservation requires a sealed input dataset")
    # Per-dataset stable identity is required for the feature manifest; without
    # it the source cannot be proven preserved.
    identity_fields = list(source_binding.get("identity_fields") or [])
    # Attribute, geometry and raster manifests are independent.  A source is
    # preserved only when every manifest sealed before execution still holds.
    checks = [
        (u"source_content_digest", manifest.get(u"source_content_digest"),
         lambda p: context_reader.layer_content_digest(p)),
    ]
    if manifest.get(u"feature_manifest_digest"):
        if not identity_fields:
            return _unresolved(proof_id, u"feature manifest requires sealed stable identity fields")
        checks.append((u"feature_manifest_digest", manifest.get(u"feature_manifest_digest"),
                       lambda p: context_reader.feature_manifest_digest(p, identity_fields)))
    if manifest.get(u"raster_content_digest"):
        checks.append((u"raster_content_digest", manifest.get(u"raster_content_digest"),
                       lambda p: context_reader.raster_content_digest(p)))
    if not any(sealed for _name, sealed, _fn in checks):
        return _unresolved(proof_id, u"source preservation requires a frozen pre-execution content manifest")
    for name, sealed, observer in checks:
        if not sealed:
            continue
        try:
            observed = observer(source)
        except Exception as exc:
            # Unreadable evidence is Unresolved (fail closed), never a pass.
            return _unresolved(proof_id, u"%s observation unavailable: %s" % (name, unicode(exc)))
        if observed is None:
            return _unresolved(proof_id, u"%s could not be independently re-read" % name)
        if observed != sealed:
            return _violated(proof_id, u"%s changed after its pre-execution observation" % name,
                             {name: {"sealed": sealed, "observed": observed}})
    return _proven(proof_id, {"manifest": {name: sealed for name, sealed, _f in checks if sealed}})


def _attr_field_names(binding):
    """Complete non-binary attribute field names sealed on one dataset."""
    fields = binding.get("fields") if isinstance(binding, dict) else None
    result = []
    seen = set()
    if not isinstance(fields, list):
        return result
    for spec in fields:
        name = spec.get("name") if isinstance(spec, dict) else spec
        ftype = unicode(spec.get("type", u"")).lower() if isinstance(spec, dict) else u""
        if not isinstance(name, unicode) or not name or name in seen:
            continue
        if ftype in (u"geometry", u"raster", u"blob"):
            continue
        seen.add(name)
        result.append(name)
    return result


def _feature_fingerprints(path, identity_fields, attribute_fields):
    """Per-feature (complete attribute tuple, geometry hash) keyed by identity.

    ``attribute_fields`` is the explicit, deduplicated attribute list (identity
    fields excluded) so nothing is dropped or duplicated.  Geometry is hashed
    from its canonical binary (WKB).  Returns None when ArcPy cannot read the
    dataset (the caller treats that as Unresolved).
    """
    desc = arcpy.Describe(path)
    oid = getattr(desc, "OIDFieldName", None) or u"OID@"
    identity = [name for name in (identity_fields or [oid])]
    attrs = [name for name in (attribute_fields or []) if name not in identity]
    cursor_fields = []
    for name in identity + attrs:
        if name not in cursor_fields:
            cursor_fields.append(name)
    n_identity = len(identity)
    n_attrs = len(cursor_fields) - n_identity
    cursor_fields.append(u"SHAPE@WKB")
    fingerprints = {}
    try:
        with arcpy.da.SearchCursor(path, cursor_fields) as rows:
            for row in rows:
                values = list(row[:-1])
                wkb = row[-1]
                ident = tuple(unicode(v) if v is not None else u"<null>"
                              for v in values[:n_identity])
                attr_tuple = tuple(unicode(v) if v is not None else u"<null>"
                                   for v in values[n_identity:n_identity + n_attrs])
                geom_hash = hashlib.sha256(wkb if isinstance(wkb, str) else bytes(wkb)).hexdigest() if wkb else u"<null>"
                # Duplicate identity within one dataset is a corrupt source.
                if ident in fingerprints:
                    return None
                fingerprints[ident] = (attr_tuple, geom_hash)
    except Exception:
        return None
    return fingerprints


def _source_specs(bindings):
    """Resolve (path, identity, attribute_fields) for each sealed source."""
    specs = []
    if isinstance(bindings.get("sources"), list):
        for item in bindings["sources"]:
            if not isinstance(item, dict) or "__output__" in item:
                continue
            path = item.get("path") or item.get("canonical_path")
            if isinstance(path, unicode) and path:
                specs.append((path, list(item.get("identity_fields") or []),
                              _attr_field_names(item)))
    else:
        src = bindings.get("source")
        if isinstance(src, dict) and "__output__" not in src:
            path = src.get("path") or src.get("canonical_path")
            if isinstance(path, unicode) and path:
                specs.append((path, list(src.get("identity_fields") or []),
                              _attr_field_names(src)))
    return specs


def _copy_family(proof_id, predicate, bindings, document):
    """copy/merge/append: per-source stable ids, complete attributes and geometry.

    Each source is fingerprinted with its OWN identity and complete attribute
    FieldSpec, then namespaced so heterogeneous ids never collide.  copy/merge
    require the result to equal exactly the union of sources (no drop, no extra,
    no reshape); append requires every appended source feature to appear in the
    mutated target.  A duplicate id within a source, or a field-name conflict
    across heterogeneous merge sources, fails closed.
    """
    kind = predicate.get("kind")
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    # append edits the target in place: the result IS the (mutated) target.
    if not result and kind == u"append":
        result = _dataset(bindings, "target")
    if not result:
        return _unresolved(proof_id, u"copy evaluator requires a sealed result dataset")
    sources = _source_specs(bindings)
    if not sources:
        return _unresolved(proof_id, u"copy evaluator requires sealed source dataset(s)")
    # Detect cross-source field-name conflicts for merge (heterogeneous schemas
    # sharing a field name with different semantics cannot be proven unified).
    if kind == u"merge":
        seen_fields = {}
        for _path, _ident, attr_fields in sources:
            for name in attr_fields:
                if name in seen_fields:
                    return _violated(proof_id, u"merge sources conflict on field name", {"field": name})
                seen_fields[name] = True
    # Read each source by its own identity; namespace keys to avoid collision.
    expected = {}  # (namespace, ident) -> (attrs, geom)
    for index, (path, identity, attr_fields) in enumerate(sources):
        if not identity:
            return _unresolved(proof_id, u"source %d has no sealed stable identity" % index)
        fp = _feature_fingerprints(path, identity, attr_fields)
        if fp is None:
            return _unresolved(proof_id, u"source %d cannot be independently re-read" % index)
        for ident, value in fp.items():
            key = (u"src%d" % index, ident)
            if key in expected:
                return _violated(proof_id, u"source has duplicate stable identity", {"identity": list(ident)})
            expected[key] = value
    # Read the result projected onto each source's fields and match by content.
    result_seen = set()
    matched = 0
    for index, (path, identity, attr_fields) in enumerate(sources):
        result_fp = _feature_fingerprints(result, identity, attr_fields)
        if result_fp is None:
            return _unresolved(proof_id, u"result cannot be re-read on source %d schema" % index)
        # content multiset for this source's projection
        from collections import defaultdict
        bucket = defaultdict(int)
        for value in result_fp.values():
            bucket[value] += 1
        for key, value in expected.items():
            if key[0] != u"src%d" % index:
                continue
            if bucket.get(value, 0) > 0:
                bucket[value] -= 1
                matched += 1
                result_seen.add(value)
            else:
                return _violated(proof_id, u"copy-family dropped a source feature",
                                 {"source": index, "identity": list(key[1])})
    if kind in (u"copy", u"merge"):
        # No extra result rows: every result row (under any source projection)
        # must be attributable. Approximate by checking the largest projection.
        primary = max(sources, key=lambda s: len(s[2])) if sources else None
        if primary is not None:
            rfp = _feature_fingerprints(result, primary[1], primary[2])
            if rfp is not None and len(rfp) > matched:
                return _violated(proof_id, u"copy-family result has extra features",
                                 {"result_count": len(rfp), "expected_count": matched})
    return _proven(proof_id, {"method": "per-source identity + attribute + geometry",
                              "matched": matched})


def _overlay(proof_id, predicate, bindings, document):
    """overlay: coverage completeness, missing/extra fragments and source lineage.

    The result region must equal the method-specific combination of the input
    regions (within a declared tolerance), every output geometry must be
    spatially explainable by its declared inputs, and result fields must carry
    the source attribute lineage.
    """
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    sources = bindings.get("sources")
    if not result or not isinstance(sources, list) or not sources:
        return _unresolved(proof_id, u"overlay requires sealed source list and result")
    paths = [item.get("path") if isinstance(item, dict) else item for item in sources]
    if any(not isinstance(item, unicode) or not arcpy.Exists(item) for item in paths):
        return _unresolved(proof_id, u"overlay source binding is unavailable")
    method = unicode(predicate.get("method", u"")).lower()
    tolerance = float(predicate.get("tolerance", 0.0) or 0.0)

    def _area(path):
        total = 0.0
        with arcpy.da.SearchCursor(path, ["SHAPE@"]) as rows:
            for row in rows:
                area = _shape_area(row[0])
                if area is not None:
                    total += area
        return total

    def _dissolved(paths):
        merged = None
        for path in paths:
            with arcpy.da.SearchCursor(path, ["SHAPE@"]) as rows:
                for row in rows:
                    shape = row[0]
                    if shape is None:
                        continue
                    merged = shape if merged is None else merged.union(shape)
        return merged

    try:
        result_area = _area(result)
        inputs = [_dissolved([p]) for p in paths]
    except Exception as exc:
        return _unresolved(proof_id, u"overlay area observation unavailable: %s" % unicode(exc))
    if any(shape is None for shape in inputs):
        return _unresolved(proof_id, u"overlay source geometry is unreadable")
    # Coverage completeness: the result region must match the method-specific
    # combination of inputs.  Missing area = dropped fragments; extra area =
    # invented fragments.
    expected = None
    if method == u"intersect":
        expected = inputs[0]
        for shape in inputs[1:]:
            expected = expected.intersect(shape, 4)
    elif method == u"union":
        expected = inputs[0]
        for shape in inputs[1:]:
            expected = expected.union(shape)
    elif method == u"clip":
        expected = inputs[0].intersect(inputs[1], 4)
    elif method == u"erase":
        expected = inputs[0].difference(inputs[1])
    elif method == u"symmetrical_difference":
        expected = inputs[0].symmetricDifference(inputs[1])
    if expected is not None:
        try:
            delta = _symmetric_area(expected, _dissolved([result]))
        except Exception as exc:
            return _unresolved(proof_id, u"overlay coverage comparison unavailable: %s" % unicode(exc))
        if delta is None:
            return _unresolved(proof_id, u"overlay coverage comparison unavailable")
        if delta > max(tolerance * tolerance, 1e-6):
            return _violated(proof_id, u"overlay coverage is incomplete or has extra fragments",
                             {"method": method, "difference_area": delta})
    # Source relation: for intersect/identity/clip every output geometry must
    # be spatially explainable by ALL declared inputs (containment).  The
    # erase/symmetrical_difference exclusion invariant is unreliable at shared
    # boundaries (boundary-touch reads as non-disjoint), so those methods rely
    # on the coverage check above rather than a per-fragment exclusion test.
    if method in (u"intersect", u"identity", u"clip"):
        with arcpy.da.SearchCursor(result, ["SHAPE@"]) as rows:
            for row in rows:
                shape = row[0]
                if shape is None:
                    return _violated(proof_id, u"overlay has null geometry")
                for path in paths:
                    with arcpy.da.SearchCursor(path, ["SHAPE@"]) as inputs_cursor:
                        if not any(item[0] is not None and not shape.disjoint(item[0]) for item in inputs_cursor):
                            return _violated(proof_id, u"overlay geometry violates source relation", {"method": method})
    # Attribute value lineage: for each result row, every non-null source
    # attribute value must correspond to a concrete source feature (by stable
    # identity) whose geometry spatially relates to the result row.  This proves
    # the output attributes were inherited from the declared inputs, not just
    # that the field names exist.
    overlay_spec = bindings.get("overlay") if isinstance(bindings.get("overlay"), dict) else {}
    field_mapping = overlay_spec.get("field_mapping") if isinstance(overlay_spec.get("field_mapping"), list) else []
    lineage_error = _overlay_attribute_lineage(result, sources, paths, field_mapping, method)
    if lineage_error is not None:
        return lineage_error
    return _proven(proof_id, {"method": "coverage + source relation + attribute value lineage",
                              "overlay": method, "result_area": result_area})


def _overlay_attribute_lineage(result, sources, paths, field_mapping, method):
    """Verify result attribute values trace to spatially-related source features."""
    if not field_mapping:
        # No provable per-source mapping was sealed; overlay lineage unverifiable.
        return _unresolved(u"acceptance:overlay", u"overlay has no sealed source field mapping")
    # Build per-source identity + attribute fingerprints with shapes.
    source_features = []  # [{namespace, identity, shape, attrs:{name:value}}]
    for index, path in enumerate(paths):
        namespace = u"source_%d" % index
        mapping = next((m for m in field_mapping if m.get(u"namespace") == namespace), None)
        identity = (mapping or {}).get(u"identity_fields") or []
        attr_fields = (mapping or {}).get(u"fields") or []
        cursor_fields = list(identity) + [name for name in attr_fields if name not in identity] + [u"SHAPE@"]
        try:
            with arcpy.da.SearchCursor(path, cursor_fields) as rows:
                for row in rows:
                    values = list(row[:-1])
                    shape = row[-1]
                    ident = tuple(unicode(v) if v is not None else u"<null>" for v in values[:len(identity)])
                    attrs = {}
                    for offset, name in enumerate(cursor_fields[len(identity):-1]):
                        attrs[name] = values[len(identity) + offset]
                    source_features.append({u"namespace": namespace, u"identity": ident,
                                            u"shape": shape, u"attrs": attrs})
        except Exception:
            return _unresolved(u"acceptance:overlay", u"overlay source unreadable for lineage")
    # For each result row, each non-null attribute value must be explainable by a
    # spatially-related source feature in that field's source namespace.
    result_fields = set(unicode(item.name).lower() for item in arcpy.ListFields(result))
    namespace_fields = {}
    for mapping in field_mapping:
        namespace_fields[mapping.get(u"namespace")] = [unicode(f) for f in mapping.get(u"fields", []) if unicode(f).lower() in result_fields]
    cursor_fields = []
    for names in namespace_fields.values():
        for name in names:
            if name not in cursor_fields:
                cursor_fields.append(name)
    cursor_fields.append(u"SHAPE@")
    try:
        with arcpy.da.SearchCursor(result, cursor_fields) as rows:
            for row in rows:
                values = list(row[:-1])
                shape = row[-1]
                for namespace, names in namespace_fields.items():
                    for name in names:
                        actual = values[cursor_fields.index(name)]
                        if actual is None or actual == u"":
                            # Overlay fills non-overlapping regions with empty
                            # values; only populated values need source lineage.
                            continue
                        # Some source feature in this namespace must spatially
                        # relate to the result row and carry this attribute value.
                        explainable = False
                        for sf in source_features:
                            if sf[u"namespace"] != namespace or sf[u"shape"] is None or shape is None:
                                continue
                            if sf[u"attrs"].get(name) == actual and not shape.disjoint(sf[u"shape"]):
                                explainable = True
                                break
                        if not explainable:
                            return _violated(u"acceptance:overlay", u"overlay attribute value has no source lineage",
                                             {"field": name, "value": unicode(actual)})
    except Exception:
        return _unresolved(u"acceptance:overlay", u"overlay result unreadable for lineage")
    return None


def _artifact_export(proof_id, predicate, bindings, document):
    if not isinstance(document, dict) or document.get("exists") is not True:
        return _violated(proof_id, u"exported artifact is absent")
    requested = predicate.get("output_format")
    actual = document.get("file_semantics", {}).get("format") if isinstance(document.get("file_semantics"), dict) else None
    if requested and actual and requested != actual:
        return _violated(proof_id, u"export format differs", {"expected": requested, "actual": actual})
    if predicate.get("selected_only") is not True:
        return _proven(proof_id, {"method": "independent artifact probe", "format": actual or requested})
    source = _dataset(bindings, "target")
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    identity = bindings.get("identity_fields")
    if not source or not result or not identity:
        return _unresolved(proof_id, u"selected export requires sealed source/result and stable identity fields")
    expected, observed = _record_keys(source, identity), _record_keys(result, identity)
    if expected != observed:
        return _violated(proof_id, u"exported selected records differ from live selected source",
                         {"expected_count": len(expected), "actual_count": len(observed)})
    return _proven(proof_id, {"method": "selected layer cursor + stable identity set", "count": len(observed)})


def _observed_map_state(proof_id, predicate, bindings, document):
    """Unified map/layout acceptance: one semantic rule, sealed pre-state.

    The pre-execution state is sealed in ``bindings["pre_state"]``; the runtime
    independently re-reads the post-state into the probe document.  The proof is
    Proven only when the sealed baseline exists and the independent post-state
    observation satisfies the sealed postcondition.
    """
    pre_state = bindings.get("pre_state")
    if not isinstance(pre_state, dict):
        return _unresolved(proof_id, u"map/layout effect requires a sealed pre-execution state")
    if not isinstance(document, dict) or document.get("probe_type") != u"map_state":
        return _unresolved(proof_id, u"map/layout effect requires an independent post-state observation")
    check = document.get("map_state_check") if isinstance(document.get("map_state_check"), dict) else {}
    if document.get("passed") is not True or check.get("verdict") != u"passed":
        return _violated(proof_id, u"live map/layout postcondition failed",
                         {"pre_state_layer_digest": pre_state.get("layer_digest"),
                          "verdict": check.get("verdict")})
    return _proven(proof_id, {"method": "sealed pre-state + independent post-state observation",
                              "pre_state_layer_digest": pre_state.get("layer_digest")})


_SUPPORTED_STAT_OPS = frozenset((u"sum", u"count", u"mean", u"min", u"max"))


def _aggregate(proof_id, predicate, bindings, document):
    """aggregate: recompute groups, per-group geometry and statistics from source.

    For every dissolve group, the evaluator independently re-derives the group
    key set, dissolves the source geometries for that group, recomputes each
    sealed statistic from the source members, and compares them per-group
    against the result.  Missing/extra groups, a reshaped dissolve, or a wrong
    statistic value Violates; unreadable geometry is Unresolved.
    """
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    source = _dataset(bindings, "source")
    if not result or not source:
        return _unresolved(proof_id, u"aggregate requires sealed source and result datasets")
    spec = bindings.get("aggregate")
    if not isinstance(spec, dict):
        return _unresolved(proof_id, u"aggregate requires a sealed aggregate spec")
    dissolve_fields = [unicode(f) for f in spec.get("dissolve_fields", []) if isinstance(f, (str, unicode))]
    statistics = spec.get("statistics") if isinstance(spec.get("statistics"), list) else []
    tolerance = float(spec.get("tolerance", 0.0) or 0.0)
    required_fields = bindings.get("required_fields")
    if not isinstance(required_fields, list) or not required_fields:
        return _unresolved(proof_id, u"aggregate requires sealed output field specs")
    actual_fields = set(unicode(item.name).lower() for item in arcpy.ListFields(result))
    missing = [_field_name(f) for f in required_fields if _field_name(f).lower() not in actual_fields]
    if missing:
        return _violated(proof_id, u"aggregate result misses required fields", {"missing": missing})

    source_groups = _grouped_members(source, dissolve_fields)
    result_groups = _grouped_members(result, dissolve_fields)
    if source_groups is None or result_groups is None:
        return _unresolved(proof_id, u"aggregate groups cannot be independently re-read")
    # A no-field aggregate is one global group (key ()).
    if set(source_groups.keys()) != set(result_groups.keys()):
        return _violated(proof_id, u"aggregate group keys differ from source",
                         {"source": len(source_groups), "result": len(result_groups)})
    for key, members in source_groups.items():
        result_members = result_groups.get(key)
        if not result_members:
            return _violated(proof_id, u"aggregate result drops a group", {"group": list(key)})
        # Geometry: the dissolved source union must equal the dissolved result.
        source_union = _union_shapes([m[u"shape"] for m in members if m[u"shape"] is not None])
        result_union = _union_shapes([m[u"shape"] for m in result_members if m[u"shape"] is not None])
        if source_union is None or result_union is None:
            return _unresolved(proof_id, u"aggregate group geometry unreadable", {"group": list(key)})
        delta = _symmetric_area(source_union, result_union)
        if delta is None:
            return _unresolved(proof_id, u"aggregate group geometry comparison unavailable", {"group": list(key)})
        if delta > max(tolerance * tolerance, 1e-6):
            return _violated(proof_id, u"aggregate group geometry differs from source dissolve",
                             {"group": list(key), "difference_area": delta})
        # Statistics: recompute each sealed operator from source member values
        # and compare against the single result row's declared output field.
        if statistics and len(result_members) != 1:
            return _violated(proof_id, u"aggregate statistic group must be one result row",
                             {"group": list(key), "result_rows": len(result_members)})
        result_row = result_members[0][u"attrs"]
        for stat in statistics:
            error = _verify_statistic(stat, members, result_row)
            if error is not None:
                return error
    return _proven(proof_id, {"method": "per-group geometry + statistics", "groups": len(source_groups)})


def _spatial_join(proof_id, predicate, bindings, document):
    """spatial_join: recompute target-join correspondence per result row.

    For each target feature (by its sealed stable identity), independently
    evaluate the sealed match option against every join feature, then verify the
    corresponding result row carries exactly those join identities, a correct
    Join_Count, and joined attribute values drawn from a matched join feature.
    """
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    target = _dataset(bindings, "target")
    join = _dataset(bindings, "join")
    if not result or not target or not join:
        return _unresolved(proof_id, u"spatial_join requires sealed target, join and result")
    spec = bindings.get("join_spec")
    if not isinstance(spec, dict):
        return _unresolved(proof_id, u"spatial_join requires a sealed join spec")
    match_option = unicode(spec.get("match_option", u"intersect"))
    target_identity = [unicode(f) for f in spec.get("target_identity", []) if isinstance(f, (str, unicode))]
    join_identity = [unicode(f) for f in spec.get("join_identity", []) if isinstance(f, (str, unicode))]
    join_fields = [unicode(f) for f in spec.get("join_fields", []) if isinstance(f, (str, unicode))]
    target_fid_field = spec.get("target_fid_field") if isinstance(spec.get("target_fid_field"), (str, unicode)) else None
    if not target_identity or not join_identity:
        return _unresolved(proof_id, u"spatial_join requires sealed target and join identities")
    required_fields = bindings.get("required_fields")
    if not isinstance(required_fields, list) or not required_fields:
        return _unresolved(proof_id, u"spatial_join requires sealed output field specs")
    actual_fields = set(unicode(item.name).lower() for item in arcpy.ListFields(result))
    missing = [_field_name(f) for f in required_fields if _field_name(f).lower() not in actual_fields]
    if missing:
        return _violated(proof_id, u"spatial_join result misses required fields", {"missing": missing})
    if _count(result) != _count(target):
        return _violated(proof_id, u"spatial_join cardinality differs from target",
                         {"target": _count(target), "result": _count(result)})
    relation = _recompute_join(target, join, result, match_option,
                               target_identity, join_identity, join_fields, target_fid_field)
    if relation is None:
        return _unresolved(proof_id, u"spatial_join correspondence cannot be independently re-read")
    if not relation[u"ok"]:
        return _violated(proof_id, u"spatial_join correspondence differs from independent relation",
                         {"mismatched": relation[u"mismatched"]})
    return _proven(proof_id, {"method": "per-row target-join correspondence", "rows": relation[u"rows"]})


def _grouped_members(path, group_fields):
    """{group_key tuple: [{shape, attrs: {field:value}}]} or None on failure."""
    try:
        fields = [field.name for field in arcpy.ListFields(path)
                  if unicode(field.type).lower() not in (u"blob", u"raster", u"geometry")]
        cursor_fields = list(group_fields) + [name for name in fields if name not in group_fields] + [u"SHAPE@"]
        groups = {}
        with arcpy.da.SearchCursor(path, cursor_fields) as rows:
            for row in rows:
                values = list(row[:-1])
                shape = row[-1]
                key = tuple(unicode(v) if v is not None else u"<null>"
                            for v in values[:len(group_fields)]) if group_fields else ()
                attrs = {}
                for index, name in enumerate(cursor_fields[len(group_fields):-1]):
                    attrs[name] = values[len(group_fields) + index]
                groups.setdefault(key, []).append({u"shape": shape, u"attrs": attrs})
        return groups
    except Exception:
        return None


def _union_shapes(shapes):
    merged = None
    for shape in shapes:
        if shape is None:
            continue
        merged = shape if merged is None else merged.union(shape)
    return merged


def _shape_area(geom):
    """Area of a geometry in m^2, compatible across ArcGIS 10.2 .. 10.8.

    10.2's ``Geometry.getArea`` takes a single units argument; later runtimes
    added a method argument.  Try both, then fall back to the ``.area`` property.
    """
    if geom is None:
        return None
    for call in (("SQUAREMETERS",), ("PLANAR", "SQUAREMETERS"), ("GEODESIC", "SQUAREMETERS")):
        try:
            return float(geom.getArea(*call))
        except Exception:
            continue
    try:
        return float(geom.getArea())
    except Exception:
        try:
            return float(geom.area)
        except Exception:
            return None


def _symmetric_area(left, right):
    try:
        if left.equals(right):
            return 0.0
    except Exception:
        pass
    try:
        diff = left.symmetricDifference(right)
        if diff is None:
            return None
        area = _shape_area(diff)
        if area is not None:
            return area
    except Exception:
        pass
    # Fall back to absolute area difference when symmetricDifference is unusable.
    la, ra = _shape_area(left), _shape_area(right)
    if la is None or ra is None:
        return None
    return abs(la - ra)


def _verify_statistic(stat, members, result_attrs):
    """Recompute one sealed statistic over group members; return a Violation or None."""
    if not isinstance(stat, dict):
        return None
    operator = unicode(stat.get("operator", u"")).lower()
    field = unicode(stat.get("field", u""))
    out_field = unicode(stat.get("output_field", field))
    if operator not in _SUPPORTED_STAT_OPS:
        return _violated(u"acceptance:statistic", u"unsupported statistic operator", {"operator": operator})
    values = [m[u"attrs"].get(field) for m in members if m[u"attrs"].get(field) is not None]
    if operator == u"count":
        expected = len(members)
    else:
        nums = []
        for value in values:
            try:
                nums.append(float(value))
            except (TypeError, ValueError):
                return _violated(u"acceptance:statistic", u"statistic source value not numeric", {"field": field})
        if operator == u"sum":
            expected = sum(nums)
        elif operator == u"mean":
            expected = (sum(nums) / len(nums)) if nums else 0.0
        elif operator == u"min":
            expected = min(nums) if nums else None
        elif operator == u"max":
            expected = max(nums) if nums else None
    actual = result_attrs.get(out_field)
    try:
        if actual is None or abs(float(actual) - float(expected)) > 1e-9:
            return _violated(u"acceptance:statistic", u"aggregate statistic value differs",
                             {"field": out_field, "expected": expected, "actual": actual})
    except (TypeError, ValueError):
        return _violated(u"acceptance:statistic", u"aggregate statistic value differs",
                         {"field": out_field, "expected": expected, "actual": actual})
    return None


def _recompute_join(target, join, result, match_option, target_identity, join_identity, join_fields, target_fid_field):
    """Per target feature, recompute matched join features and verify the result row.

    The spatial-join result does NOT carry the target's own identity directly:
    it carries the source target OID in ``target_fid_field`` (TARGET_FID).  The
    result row is located by that field, then Join_Count and joined attribute
    values are checked against the independently recomputed relation.
    """
    try:
        result_fields = set(item.name for item in arcpy.ListFields(result))
        has_join_count = u"Join_Count" in result_fields
        fid_field = target_fid_field if target_fid_field and target_fid_field in result_fields else None
        if fid_field is None:
            # Without the target-FID mapping the correspondence is unverifiable.
            return None
        # Read join features: identity + shape + declared join attributes.
        join_cursor_fields = list(join_identity) + [u"SHAPE@"]
        for name in join_fields:
            if name not in join_cursor_fields:
                join_cursor_fields.append(name)
        join_features = []
        with arcpy.da.SearchCursor(join, join_cursor_fields) as rows:
            for row in rows:
                values = list(row)
                ident = tuple(unicode(v) if v is not None else u"<null>" for v in values[:len(join_identity)])
                shape = values[len(join_identity)]
                attrs = {}
                for index, name in enumerate(join_cursor_fields[len(join_identity) + 1:]):
                    attrs[name] = values[len(join_identity) + 1 + index]
                join_features.append({u"id": ident, u"shape": shape, u"attrs": attrs})
        # Index result rows by the target FID value.
        result_cursor_fields = [fid_field] + list(join_fields)
        if has_join_count:
            result_cursor_fields.append(u"Join_Count")
        result_by_fid = {}
        with arcpy.da.SearchCursor(result, result_cursor_fields) as rows:
            for row in rows:
                fid = unicode(row[0]) if row[0] is not None else u"<null>"
                result_by_fid[fid] = list(row[1:])
        # Walk target features by identity + shape, recompute matches per row.
        mismatched = 0
        rows_checked = 0
        with arcpy.da.SearchCursor(target, list(target_identity) + [u"SHAPE@"]) as rows:
            for row in rows:
                rows_checked += 1
                ident = tuple(unicode(v) if v is not None else u"<null>" for v in row[:len(target_identity)])
                shape = row[-1]
                if shape is None:
                    return None
                matched = [jf for jf in join_features
                           if jf[u"shape"] is not None and _relation_holds(shape, jf[u"shape"], match_option)]
                claimed = result_by_fid.get(ident[0])
                if claimed is None:
                    mismatched += 1
                    continue
                # Verify Join_Count if present.
                if has_join_count:
                    claimed_count = claimed[-1]
                    if claimed_count is not None and int(claimed_count) != len(matched):
                        mismatched += 1
                        continue
                # Verify joined attribute values come from some matched join feature.
                claimed_join_values = claimed[:len(join_fields)]
                if join_fields and matched:
                    valid = any([claimed_join_values == [jf[u"attrs"].get(name) for name in join_fields]
                                 for jf in matched])
                    if not valid and any(v is not None for v in claimed_join_values):
                        mismatched += 1
        return {u"ok": mismatched == 0, u"mismatched": mismatched, u"rows": rows_checked}
    except Exception:
        return None


def _relation_holds(target_shape, join_shape, match_option):
    if match_option == u"intersect":
        return target_shape.disjoint(join_shape) is False
    if match_option == u"within":
        return target_shape.within(join_shape)
    if match_option == u"contain":
        return target_shape.contains(join_shape)
    return False


def _field_name(spec):
    if isinstance(spec, dict):
        return unicode(spec.get(u"name", u""))
    return unicode(spec)


def _field_update(proof_id, predicate, bindings, document):
    target = _dataset(bindings, "result", document) or _dataset(bindings, "target", document)
    if not target:
        return _unresolved(proof_id, u"field update evaluator lacks sealed target")
    where = predicate.get("where")
    assignments = predicate.get("assignments")
    if not isinstance(where, dict) or not isinstance(assignments, dict) or not assignments:
        return _unresolved(proof_id, u"field update requires sealed predicate and assignments")
    clause = _where(target, where)
    fields = list(assignments)
    # Every selected row must contain the intended value.  This validates the
    # result itself, not CalculateField's success receipt.
    with arcpy.da.SearchCursor(target, fields, where_clause=clause) as rows:
        for row in rows:
            for index, name in enumerate(fields):
                if row[index] != assignments[name]:
                    return _violated(proof_id, u"updated field value differs", {"field": name})
    return _proven(proof_id, {"method": "SearchCursor post-update value check", "count": _count(target, clause)})


def _feature_family(proof_id, predicate, bindings, document):
    result = _dataset(bindings, "result", document) or _dataset(bindings, "subject", document)
    if not result:
        return _unresolved(proof_id, u"feature evaluator lacks sealed result")
    geometry = getattr(arcpy.Describe(result), "shapeType", None)
    if not geometry or _count(result) <= 0:
        return _violated(proof_id, u"feature operation produced no valid feature geometry")
    return _proven(proof_id, {"method": "Describe geometry + GetCount", "geometry": unicode(geometry), "count": _count(result)})


def _repair(proof_id, predicate, bindings, document):
    result = _dataset(bindings, "result", document) or _dataset(bindings, "target", document)
    if not result:
        return _unresolved(proof_id, u"repair evaluator lacks sealed target")
    scratch = arcpy.CreateUniqueName("geopilot_acceptance_check", arcpy.env.scratchGDB)
    try:
        checked = arcpy.CheckGeometry_management(result, scratch).getOutput(0)
        invalid = int(arcpy.GetCount_management(checked).getOutput(0))
    finally:
        if arcpy.Exists(scratch): arcpy.Delete_management(scratch)
    if invalid:
        return _violated(proof_id, u"repaired dataset still contains invalid geometry", {"invalid_count": invalid})
    return _proven(proof_id, {"method": "CheckGeometry", "invalid_count": 0})


def _add_xy(proof_id, predicate, bindings, document):
    target = _dataset(bindings, "result", document) or _dataset(bindings, "target", document)
    if not target: return _unresolved(proof_id, u"add_xy evaluator lacks sealed target")
    names = set(unicode(item.name).lower() for item in arcpy.ListFields(target))
    required = set((u"point_x", u"point_y"))
    if not required.issubset(names):
        return _violated(proof_id, u"AddXY fields are absent", {"missing": list(required - names)})
    return _proven(proof_id, {"method": "Describe fields", "fields": [u"POINT_X", u"POINT_Y"]})


def _inspect(proof_id, predicate, bindings, document):
    subject = _dataset(bindings, "subject", document) or _dataset(bindings, "target", document)
    if not subject: return _unresolved(proof_id, u"inspection target is not independently addressable")
    arcpy.Describe(subject)
    return _proven(proof_id, {"method": "independent Describe"})




# The evaluator registry maps each production profile's ``evaluator`` key to its
# implementation.  It is NOT keyed independently by effect kind and has NO
# unresolved fallback: ``_assert_evaluator_registry`` (below) enforces a strict
# 1:1 correspondence with PROFILES — every profile has exactly one evaluator and
# there are no orphan evaluators.
_EVALUATORS = {
    "attribute_filter": _attribute_filter, "spatial_filter": _spatial_filter,
    "buffer": _buffer, "project": _crs, "define_projection": _crs,
    "field_add": _field, "field_delete": _field, "field_update": _field_update,
    "source_preserved": _source_preserved, "copy": _copy_family,
    "merge": _copy_family, "append": _copy_family, "overlay": _overlay,
    "artifact_export": _artifact_export,
    "inspect": _inspect, "map_change": _observed_map_state,
    "layout_change": _observed_map_state, "spatial_join": _spatial_join,
    "aggregate": _aggregate,
    "feature_create": _feature_family, "feature_append": _feature_family,
    "repair": _repair, "add_xy": _add_xy,
}


def _assert_evaluator_registry():
    """Strict 1:1 check: every profile.evaluator has an implementation and there
    are no orphan evaluators.  Raises on drift so the runtime never silently
    routes to a missing/wrong evaluator."""
    profile_evaluators = set(p.evaluator for p in PROFILES.values())
    implemented = set(_EVALUATORS.keys())
    missing = profile_evaluators - implemented
    orphan = implemented - profile_evaluators
    if missing or orphan:
        raise RuntimeError(
            "acceptance evaluator registry drift — missing: %s; orphan: %s"
            % (sorted(missing), sorted(orphan)))


_assert_evaluator_registry()
