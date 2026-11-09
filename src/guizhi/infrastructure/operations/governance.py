"""Publish an explanation from verified case facts and applicable rule references."""
import shutil

from guizhi.domain.semantics import code, normalized
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.knowledge import citation
from guizhi.infrastructure.operations.semantics import artifact_json, case_report, key


def explain_findings(ctx, case_run: str, rules_run: str, title: str = "审批核查与适用依据"):
    case = artifact_json(ctx.workspace, case_run, "case")
    result = artifact_json(ctx.workspace, rules_run, "rule_resolution")
    project = case["project"]
    if normalized(project["region"]) != normalized(result["region"]):
        raise ValueError("Case and rule resolution belong to different regions")
    if result["project_code"] and code(result["project_code"]) != code(project["code"]):
        raise ValueError("Case and rules belong to different project identifiers")
    if case["as_of"] and case["as_of"] != result["as_of"]:
        raise ValueError("Case and rule resolution use different dates")
    for claim in case["claims"]:
        for ref in claim["evidence"]:
            read_artifact(ctx.workspace, ref["run"], ref["artifact"])
    for item in result["findings"]:
        rule = item["rule"]
        evidence = citation(ctx, rule["basis"])
        case["claims"].append({"id": key(project["revision"], rule["edition"], result["catalog_revision"]),
            "subject": project["id"], "kind": "explanation", "field": result["matter"], "value": rule["title"],
            "status": "supported" if item["status"] == "applicable" else "conflict", "quote": evidence["quote"],
            "edition": rule["edition"], "as_of": result["as_of"], "qualifier": rule["transition"],
            "evidence": [evidence]})
    if not result["findings"]:
        case["claims"].append({"id": key(project["id"], rules_run, "missing-basis"), "subject": project["id"],
            "kind": "recommendation", "field": "适用依据", "status": "insufficient",
            "value": "补充本地区和日期有效的规则依据", "evidence": []})
    if result["status"] != "applicable" and case["status"] != "conflict":
        case["status"] = "conflict" if result["status"] == "conflict" else "insufficient"
    case["sources"]["rules_run"] = rules_run
    case["rule_resolution"] = result
    ctx.json("case.json", case, "case")
    source = read_artifact(ctx.workspace, case_run, "map")
    output = ctx.path("map.json")
    shutil.copyfile(source, output)
    ctx.record("map", output)
    case_report(ctx, case, title)
    return {"statistics": {"claim_count": len(case["claims"]), "rule_count": len(result["findings"])},
            "case_status": case["status"]}
