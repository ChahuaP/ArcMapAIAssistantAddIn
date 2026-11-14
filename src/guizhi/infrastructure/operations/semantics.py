"""Index approval materials, bind them to parcels, and publish traceable findings."""
from datetime import date
from hashlib import sha256
import json
import re
from collections import defaultdict

import pyogrio
import shapely
from shapely.geometry import mapping

from guizhi.domain.semantics import Fact, Transition, code, normalized
from guizhi.infrastructure.control import polygons
from guizhi.infrastructure.operations.evidence import read_artifact


def key(*parts):
    return sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def artifact_json(workspace, run, kind):
    return json.loads(read_artifact(workspace, run, kind).read_text(encoding="utf-8"))


def read_documents(ctx):
    from guizhi.infrastructure.documents import fragments

    documents, records = [], []
    for number, (path, ref) in enumerate(ctx.documents, 1):
        documents.append({"number": number, "artifact": ref["step"], "revision": ref["sha256"],
                          "name": path.name, "source_run": ctx.job["run_id"]})
        for n, (locator, text) in enumerate(fragments(path, ctx.check), 1):
            records.append({"id": f"{number}.{n}", "document": number, "locator": locator, "text": text})
    ctx.json("document-index.json", {"documents": documents, "fragments": records}, "document_index")
    return {"statistics": {"document_count": len(documents), "fragment_count": len(records)}}


def find_fragments(index, terms, *, limit=18, width=650):
    tokens = [normalized(item) for item in terms if item.strip()]
    records = []
    for item in index["fragments"]:
        if not item["text"]:
            continue
        matches = [re.search(r"\s+".join(map(re.escape, token.split())), item["text"], re.IGNORECASE)
                   for token in tokens]
        hits = [match.start() for match in matches if match]
        if tokens and not hits:
            continue
        start = max(0, min(hits, default=0) - width // 3)
        records.append((len(hits), {**item, "text": item["text"][start:start + width]}))
    return [item for _, item in sorted(records, key=lambda row: -row[0])[:limit]]


def validate_material_facts(source, project_code, proposed):
    docs = {doc["number"]: doc for doc in source["documents"]}
    fragments = {item["id"]: item for item in source["fragments"]}
    linked = {item["document"] for item in fragments.values() if code(project_code) in code(item["text"])}
    validated = []
    if not code(project_code) or not linked:
        raise ValueError("No approval material identifies the project by code")
    for fact in proposed:
        fragment = fragments.get(fact.fragment)
        quote = normalized(fact.quote)
        if not fragment or fragment["document"] not in linked or quote not in normalized(fragment["text"]):
            raise ValueError(f"Claim {fact.field} lacks a matching, project-linked source quotation")
        if normalized(fact.value) not in quote or (fact.qualifier and normalized(fact.qualifier) not in quote):
            raise ValueError(f"Claim {fact.field} value or qualifier is not present in the quotation")
        if fact.polarity != "affirmed" and not fact.qualifier:
            raise ValueError(f"Claim {fact.field} requires its negation or condition from the source")
        if fact.observed_on:
            stamp = fact.observed_on
            formats = (stamp.isoformat(), f"{stamp.year}年{stamp.month}月{stamp.day}日",
                       f"{stamp:%B} {stamp.day}, {stamp.year}")
            if not any(normalized(value) in quote for value in formats):
                raise ValueError(f"Claim {fact.field} observation date must be present in the quotation")
        validated.append((fact, fragment, docs[fragment["document"]]))
    return validated, [docs[number] for number in sorted(linked)]


def material_claims(ctx, source, document_run, project_code, project_id, proposed, required, as_of):
    validated, docs = validate_material_facts(source, project_code, proposed)
    claims, by_field = [], defaultdict(list)
    for fact, fragment, doc in validated:
        read_artifact(ctx.workspace, document_run, doc["artifact"])
        claim = {"id": key(project_id, fact.model_dump(mode="json"), doc["revision"]),
                 "subject": project_id, "kind": "recorded", "status": "supported",
                 **fact.model_dump(mode="json"), "evidence": [{"run": document_run, "artifact": doc["artifact"],
                     "sha256": doc["revision"], "fragment": fact.fragment, "locator": fragment["locator"]}]}
        claims.append(claim)
        if not as_of or not fact.observed_on or fact.observed_on <= as_of:
            by_field[fact.field].append(claim)
        else:
            claim["status"] = "outside_period"
    for field, candidates in by_field.items():
        if len({(normalized(item["value"]), item["polarity"]) for item in candidates}) > 1:
            for item in candidates:
                item["status"] = "conflict"
    for field in sorted(set(required) - set(by_field)):
        claims.append({"id": key(project_id, field, "missing"), "subject": project_id, "kind": "recorded",
                       "field": field, "status": "insufficient", "value": None, "evidence": []})
    relations = [{"type": "described_by", "from": project_id, "to": doc["revision"],
                  "run": document_run, "artifact": doc["artifact"]} for doc in docs]
    return claims, relations


def parcel_transitions(ctx, prior_case, transitions, project_id, parcels):
    if not transitions:
        return []
    if not prior_case:
        raise ValueError("Parcel changes require the previous case revision")
    previous = artifact_json(ctx.workspace, prior_case, "case")
    if previous["project"]["id"] != project_id:
        raise ValueError("Parcel changes require the same project")
    old = {parcel["code"]: parcel for parcel in previous["parcels"]}
    new = {parcel["code"]: parcel for parcel in parcels}
    links = []
    for change in transitions:
        if len(set(change.previous)) != len(change.previous) or len(set(change.current)) != len(change.current):
            raise ValueError("Repeated parcel code in a transition")
        if set(change.previous) - old.keys() or set(change.current) - new.keys():
            raise ValueError("Parcel transition references an unknown parcel")
        for before in change.previous:
            for after in change.current:
                links.append({"type": "revises", "from": new[after]["revision"], "to": old[before]["revision"],
                              "previous_case": prior_case})
    return links


def parcel_changes(ctx, prior_case, project_id, parcels):
    if not prior_case:
        return []
    previous = artifact_json(ctx.workspace, prior_case, "case")
    if previous["project"]["id"] != project_id:
        raise ValueError("The update requires the same project")
    old = {parcel["id"]: parcel for parcel in previous["parcels"]}
    new = {parcel["id"]: parcel for parcel in parcels}
    changes = [{"code": item["code"], "status": "removed"} for identity, item in old.items() if identity not in new]
    for identity, item in new.items():
        if identity not in old:
            changes.append({"code": item["code"], "status": "added"})
        elif item["revision"] != old[identity]["revision"]:
            changes.append({"code": item["code"], "status": "updated"})
    return changes


def spatial_findings(ctx, frame, parcels, spatial_run):
    if not spatial_run:
        return [], {}
    record = ctx.workspace.read(spatial_run)
    if record["task"]["operation"] != "control_check" or record["status"] != "succeeded":
        raise ValueError("The spatial evidence must be a committed control overlay")
    data = record["result"]
    result = pyogrio.read_dataframe(read_artifact(ctx.workspace, spatial_run, "export"), layer=data["export_layer"])
    if (frame.crs != result.crs or frame.index.tolist() != result["source_fid"].tolist()
            or shapely.to_wkb(frame.geometry).tolist() != shapely.to_wkb(result.geometry).tolist()):
        raise ValueError("Spatial evidence refers to a different parcel version or geometry")
    ref = next(item for item in data["evidence"] if item["step"] == "export")
    claims = []
    for parcel, row in zip(parcels, result.itertuples(), strict=True):
        claims.append({"id": key(parcel["revision"], ref["sha256"]), "subject": parcel["id"], "kind": "computed",
                       "field": "重叠面积", "status": "supported", "value": row.overlap_m2, "unit": "m²",
                       "relation": row.relation, "evidence": [{"run": spatial_run, "artifact": "export",
                           "sha256": ref["sha256"], "locator": {"fid": int(row.source_fid)}}]})
    return claims, data["statistics"]


def case_report(ctx, case, title):
    names = {"supported": "有依据", "conflict": "材料冲突", "insufficient": "证据不足",
             "outside_period": "时点之外"}
    rows = [[item["field"], item["value"], item.get("unit", ""), names.get(item["status"], item["status"]),
             item.get("quote", "")] for item in case["claims"]]
    ctx.json("report.json", {"title": title,
                            "note": "项目编号：" + str(case["project"]["code"]) +
                            ("；核查时点：" + case["as_of"] if case["as_of"] else ""),
                            "tables": [{"title": "核查依据", "columns": ["事项", "结果", "单位", "证据状态", "原文"],
                                        "rows": rows}]}, "report")


def approval_case(ctx, document_run: str, project_code: str, region: str, parcel_field: str,
                  project_field: str, facts: list[Fact], required: list[str] = (),
                  as_of: date | None = None, spatial_run: str | None = None, precise: bool = True,
                  prior_case: str | None = None, transitions: list[Transition] = ()):
    frame = ctx.frames[0]
    if not code(project_code) or not normalized(region) or len(facts) > 64 or len(required) > 32:
        raise ValueError("Invalid project identity or too many findings")
    if parcel_field not in frame.columns or project_field not in frame.columns:
        raise ValueError("Parcel and project identifier fields are required")
    if frame[parcel_field].isna().any() or frame[project_field].isna().any():
        raise ValueError("Parcel and project identifiers cannot be missing")
    if any(code(value) != code(project_code) for value in frame[project_field]):
        raise ValueError("The layer includes a different project identifier")
    shapes = polygons(frame, "Parcel")
    identifiers = frame[parcel_field].map(lambda value: str(value).strip())
    if any(not code(item) for item in identifiers) or not identifiers.map(code).is_unique:
        raise ValueError("Parcel identifiers must be nonempty and unique in the selected layer")
    source = artifact_json(ctx.workspace, document_run, "document_index")
    project_id = key("project", normalized(region), code(project_code))
    revision = ctx.selection_hashes[0]
    parcels = []
    attributes = frame.drop(columns=frame.geometry.name)
    for (fid, value), shape, row in zip(identifiers.items(), shapes, attributes.itertuples(index=False, name=None), strict=True):
        identity = key("parcel", normalized(region), code(value))
        parcel_revision = key(identity, frame.crs.to_wkt(), shapely.to_wkb(shapely.normalize(shape), hex=True),
                              list(zip(attributes.columns, map(str, row), strict=True)))
        parcels.append({"id": identity, "code": value, "revision": parcel_revision, "fid": int(fid)})
    claims, links = material_claims(ctx, source, document_run, project_code, project_id, facts, required, as_of)
    links.extend({"type": "contains", "from": project_id, "to": parcel["id"]} for parcel in parcels)
    links.extend(parcel_transitions(ctx, prior_case, transitions, project_id, parcels))
    if not precise and spatial_run:
        raise ValueError("Approximate location cannot be used for precise area findings")
    calculated, stats = spatial_findings(ctx, frame, parcels, spatial_run)
    claims += calculated
    case = {"project": {"id": project_id, "code": project_code, "region": region,
                        "revision": key(project_id, revision, [doc["revision"] for doc in source["documents"]])},
            "parcels": parcels, "claims": claims, "links": links, "as_of": as_of.isoformat() if as_of else None,
            "parcel_changes": parcel_changes(ctx, prior_case, project_id, parcels),
            "precise": precise, "status": ("conflict" if any(c["status"] == "conflict" for c in claims) else
                     "insufficient" if not claims or any(c["status"] == "insufficient" for c in claims) else "supported"),
            "sources": {"document_run": document_run, "spatial_run": spatial_run, "parcel_revision": revision}}
    ctx.json("case.json", case, "case")
    ctx.json("map.json", {"crs": frame.crs.to_string(), "bounds": frame.total_bounds.tolist(),
                         "features": [{"role": "project", "id": parcel["id"], "label": parcel["code"], "geometry": mapping(shape)}
                                      for parcel, shape in zip(parcels, shapes, strict=True)]}, "map")
    case_report(ctx, case, "审批材料与地块核查")
    return {"statistics": {**stats, "parcel_count": len(parcels), "claim_count": len(claims),
                           "changed_parcels": len(case["parcel_changes"])}, "case_status": case["status"]}
