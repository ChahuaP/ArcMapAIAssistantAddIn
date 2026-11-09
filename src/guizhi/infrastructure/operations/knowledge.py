"""Grounded extraction, terminology, identity and as-of rule resolution."""
from datetime import date

from guizhi.domain.knowledge import Citation, ModelScope, RuleEdition, Term
from guizhi.domain.semantics import Fact, code, normalized
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.semantics import artifact_json, key, validate_material_facts


def citation(ctx, basis):
    ref = Citation.model_validate(basis)
    index = artifact_json(ctx.workspace, ref.document_run, "document_index")
    item = next((row for row in index["fragments"] if row["id"] == ref.fragment), None)
    if not item or normalized(ref.quote) not in normalized(item["text"]):
        raise ValueError("Catalog citation is missing from the source fragment")
    doc = next(row for row in index["documents"] if row["number"] == item["document"])
    read_artifact(ctx.workspace, ref.document_run, doc["artifact"])
    return {"run": ref.document_run, "artifact": doc["artifact"], "sha256": doc["revision"],
            "fragment": ref.fragment, "locator": item["locator"], "quote": ref.quote}


def catalog(ctx, catalog_run):
    return artifact_json(ctx.workspace, catalog_run, "knowledge_catalog")


def register_catalog(ctx, editions: list[RuleEdition], terms: list[Term] = (), models: list[ModelScope] = ()):
    if not 1 <= len(editions) <= 64 or len(terms) > 128 or len(models) > 24:
        raise ValueError("Catalog size is invalid")
    entries = {item.edition: item for item in editions}
    if len(entries) != len(editions):
        raise ValueError("Rule editions must be unique")
    for item in editions:
        if item.valid_until and item.valid_from >= item.valid_until:
            raise ValueError("Rule validity uses an exclusive end after its start")
        for target in item.supersedes:
            earlier = entries.get(target)
            if (not earlier or earlier.family != item.family or earlier.valid_from >= item.valid_from
                    or normalized(earlier.region) != normalized(item.region)
                    or normalized(earlier.matter) != normalized(item.matter)
                    or code(earlier.project_code or "") != code(item.project_code or "")):
                raise ValueError("A replacement must name an earlier edition in the same scope")
    if len({normalized(item.source) for item in terms}) != len(terms):
        raise ValueError("Vocabulary entries must be unique")
    if len({(item.provider, item.model, item.version) for item in models}) != len(models):
        raise ValueError("Model evaluation profiles must be unique")
    rules = [{**item.model_dump(mode="json"), "evidence": citation(ctx, item.basis)} for item in editions]
    vocabulary = []
    for term in terms:
        evidence = citation(ctx, term.basis)
        if normalized(term.source) not in normalized(evidence["quote"]):
            raise ValueError("The terminology source must appear in its quotation")
        vocabulary.append({**term.model_dump(mode="json"), "evidence": evidence})
    evaluations = [{**item.model_dump(mode="json"), "evidence": citation(ctx, item.assessment)} for item in models]
    version = key(rules, vocabulary, evaluations)
    ctx.json("knowledge-catalog.json", {"revision": version, "rules": rules, "terms": vocabulary,
                                        "models": evaluations}, "knowledge_catalog")
    return {"statistics": {"rules": len(rules), "terms": len(vocabulary), "models": len(evaluations)},
            "catalog_revision": version}


def scope_rule(item, region, matter, project_code):
    return (normalized(item["region"]) == normalized(region)
            and normalized(item["matter"]) == normalized(matter)
            and (not item["project_code"] or code(item["project_code"]) == code(project_code or "")))


def resolve_rules(entries, region, matter, as_of, project_code):
    stamp = as_of.isoformat()
    scoped = [item for item in entries if scope_rule(item, region, matter, project_code)]
    active = [item for item in scoped if item["valid_from"] <= stamp
              and (not item["valid_until"] or stamp < item["valid_until"])]
    superseded = set()
    by_edition = {item["edition"]: item for item in scoped}
    for item in active:
        previous = list(item["supersedes"])
        while previous:
            target = previous.pop()
            if target not in superseded:
                superseded.add(target)
                previous.extend(by_edition[target]["supersedes"])
    applicable = [item for item in active if item["edition"] not in superseded]
    counts = {family: sum(item["family"] == family for item in applicable)
              for family in {item["family"] for item in applicable}}
    findings = [{"status": "conflict" if counts[item["family"]] > 1 else "applicable", "rule": item}
                for item in applicable]
    inactive = [{"edition": item["edition"], "status": "superseded" if item["edition"] in superseded else
                 "future" if stamp < item["valid_from"] else "expired", "evidence": item["evidence"]}
                for item in scoped if item not in applicable]
    return {"status": ("conflict" if any(item["status"] == "conflict" for item in findings)
                       else "applicable" if findings else "out_of_period" if scoped else "out_of_scope"),
            "findings": findings, "excluded": inactive, "as_of": stamp, "region": region,
            "matter": matter, "project_code": project_code}


def find_rules(ctx, catalog_run: str, region: str, matter: str, as_of: date, project_code: str | None = None):
    index = catalog(ctx, catalog_run)
    result = resolve_rules(index["rules"], region, matter, as_of, project_code)
    for item in result["findings"]:
        citation(ctx, item["rule"]["basis"])
    result.update(catalog_run=catalog_run, catalog_revision=index["revision"])
    ctx.json("rules.json", result, "rule_resolution")
    return {"statistics": {"rule_count": len(result["findings"])}, "rule_status": result["status"]}


def extract_facts(ctx, document_run: str, project_code: str, facts: list[Fact]):
    source = artifact_json(ctx.workspace, document_run, "document_index")
    valid, _ = validate_material_facts(source, project_code, facts)
    entries = []
    for fact, fragment, doc in valid:
        read_artifact(ctx.workspace, document_run, doc["artifact"])
        entries.append({**fact.model_dump(mode="json"), "kind": "recorded", "status": "supported",
                        "evidence": {"run": document_run, "artifact": doc["artifact"], "sha256": doc["revision"],
                                     "fragment": fact.fragment, "locator": fragment["locator"]}})
    ctx.json("facts.json", entries, "facts")
    return {"statistics": {"fact_count": len(entries)}}


def map_terms(ctx, catalog_run: str, terms: list[str]):
    index = catalog(ctx, catalog_run)
    by_term = {normalized(item["source"]): item for item in index["terms"]}
    result = []
    for name in terms:
        item = by_term.get(normalized(name))
        if item:
            citation(ctx, item["basis"])
        result.append({"term": name, "status": "known" if item else "unregistered", "mapping": item})
    ctx.json("terms.json", result, "term_mapping")
    return {"statistics": {"matched": sum(item["status"] == "known" for item in result)}}


def match_objects(ctx, case_run: str, region: str, codes: list[str]):
    case = artifact_json(ctx.workspace, case_run, "case")
    scoped = normalized(region) == normalized(case["project"]["region"])
    lookup = {code(item["code"]): item for item in [case["project"], *case["parcels"]]} if scoped else {}
    result = [{"query": item, "status": "matched" if code(item) in lookup else "unresolved",
               "object": lookup.get(code(item)), "case_run": case_run} for item in codes]
    ctx.json("matches.json", result, "object_matches")
    return {"statistics": {"matched": sum(item["status"] == "matched" for item in result)}}


def model_scope(ctx, catalog_run: str, provider: str, model: str, version: str, region: str, task: str):
    index = catalog(ctx, catalog_run)
    item = next((item for item in index["models"] if (item["provider"], item["model"], item["version"]) ==
                 (provider, model, version)), None)
    accepted = item is not None and task in item["tasks"] and normalized(region) in set(map(normalized, item["regions"]))
    if accepted:
        citation(ctx, item["assessment"])
    result = {"status": "in_scope" if accepted else "out_of_scope", "task": task, "region": region, "profile": item}
    ctx.json("model-scope.json", result, "model_scope")
    return {"statistics": {"in_scope": accepted}, "model_status": result["status"]}
