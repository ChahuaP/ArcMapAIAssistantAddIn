"""Source entry point for the Guizhi vector task runner."""
import argparse
import json
from pathlib import Path
import sys

# The embedded interpreter is deliberately isolated from global packages and PYTHONPATH.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guizhi.application.tasks import inspect, product_version, run_operation, run_vector, worker
from guizhi.infrastructure.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser(prog="guizhi")
    parser.add_argument("--version", action="version", version=product_version())
    parser.add_argument("--workspace", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Import, count and export a GeoPackage layer")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--layer", required=True)
    run.add_argument("--object-id")
    check = commands.add_parser("check", help="Check polygon coverage and export evidence")
    check.add_argument("--input", required=True, type=Path)
    check.add_argument("--layer", required=True)
    check.add_argument("--control", required=True, type=Path)
    check.add_argument("--control-layer", required=True)
    check.add_argument("--object-id")
    check.add_argument("--project-plan")
    check.add_argument("--control-plan")
    for command in ("inspect", "cancel", "_worker"):
        commands.add_parser(command).add_argument("run_id")
    commands.add_parser("layers").add_argument("file", type=Path)
    commands.add_parser("material-describe").add_argument("file", type=Path)
    commands.add_parser("material-intake").add_argument("request", type=Path)
    commands.add_parser("_material-reader")
    commands.add_parser("map").add_argument("request", type=Path)
    shapefile = commands.add_parser("export-shp")
    shapefile.add_argument("run_id")
    shapefile.add_argument("kind")
    shapefile.add_argument("output", type=Path)
    commands.add_parser("imagery-models")
    resource = commands.add_parser("imagery-model-add")
    resource.add_argument("model_file", type=Path)
    resource.add_argument("profile_file", type=Path)
    activation = commands.add_parser("imagery-model-activate")
    activation.add_argument("name")
    activation.add_argument("version")
    activation.add_argument("state", choices=("enabled", "disabled"))
    commands.add_parser("crs-list")
    commands.add_parser("crs-import").add_argument("file", type=Path)
    define = commands.add_parser("crs-define", help="Import a coordinate definition from a dataset")
    define.add_argument("file", type=Path)
    define.add_argument("--layer")
    commands.add_parser("crs-solve").add_argument("request", type=Path)
    commands.add_parser("crs-rename").add_argument("request", type=Path)
    commands.add_parser("crs-remove").add_argument("reference")
    commands.add_parser("crs-show").add_argument("reference")
    commands.add_parser("execute", help="Run a registered operation from a JSON request").add_argument("request", type=Path)
    commands.add_parser("capabilities", help="List registered independent operations")
    commands.add_parser("capability-show").add_argument("operation")
    commands.add_parser("skills", help="List active skills")
    commands.add_parser("skill-show").add_argument("reference")
    commands.add_parser("skill-preview").add_argument("request", type=Path)
    commands.add_parser("skill-trial").add_argument("request", type=Path)
    commands.add_parser("skill-publish").add_argument("request", type=Path)
    commands.add_parser("workflow").add_argument("request", type=Path)
    for name in ("diagnose-run", "replay-run"):
        diagnostic = commands.add_parser(name)
        diagnostic.add_argument("run_id")
        diagnostic.add_argument("--assertion", type=Path)
        if name == "replay-run":
            diagnostic.add_argument("--seconds", type=int, default=20)
    commands.add_parser("diagnose-dispatch").add_argument("dispatch_id")
    commands.add_parser("improvement-plan").add_argument("request", type=Path)
    candidate = commands.add_parser("improvement-apply")
    candidate.add_argument("id")
    candidate.add_argument("proposal", type=Path)
    commands.add_parser("tool-plan").add_argument("request", type=Path)
    tool = commands.add_parser("tool-apply")
    tool.add_argument("id")
    tool.add_argument("proposal", type=Path)
    seed = commands.add_parser("release-seed")
    seed.add_argument("root", type=Path)
    seed.add_argument("archive", type=Path)
    seed.add_argument("sha256")
    commands.add_parser("release-active").add_argument("root", type=Path)
    commands.add_parser("platform-contract", help="Read the local platform connector contract")
    commands.add_parser("platform-handoff", help="Describe verified local artifacts for handoff").add_argument(
        "request", type=Path)
    live = commands.add_parser("arcgis-stage")
    live.add_argument("bridge", type=Path)
    live.add_argument("request", type=Path)
    commands.add_parser("arcgis-capture").add_argument("request", type=Path)
    commands.add_parser("arcgis-revision").add_argument("database", type=Path)
    notify = commands.add_parser("arcgis-notify")
    notify.add_argument("pid", type=int)
    notify.add_argument("window", type=int)
    commands.add_parser("arcgis-view").add_argument("bridge", type=Path)
    commands.add_parser("_dispatch-replay").add_argument("request", type=Path)
    args = parser.parse_args()
    if args.workspace is None and args.command != "_material-reader":
        parser.error("the following arguments are required: --workspace")
    try:
        if args.command == "_material-reader":
            from guizhi.infrastructure.materials import serve

            serve(sys.stdin.buffer, sys.stdout)
            return 0
        if args.command == "map":
            from guizhi.infrastructure.map_workspace import request

            result = request(Workspace(args.workspace, create=True), json.loads(args.request.read_text(encoding="utf-8-sig")))
            print(json.dumps(result, allow_nan=False))
            return 0
        if args.command in {"release-seed", "release-active", "tool-plan", "tool-apply"}:
            from guizhi.application import tool_repair
            from guizhi.infrastructure import releases

            if args.command == "release-seed":
                result = tool_repair.seed(args.root, args.archive, args.sha256)
            elif args.command == "release-active":
                result = releases.active(args.root)
            elif args.command == "tool-plan":
                result = tool_repair.prepare(Workspace(args.workspace), json.loads(args.request.read_text(encoding="utf-8-sig")))
            else:
                result = tool_repair.apply(Workspace(args.workspace), args.id, json.loads(args.proposal.read_text(encoding="utf-8-sig")))
            print(json.dumps(result))
            return 0
        if args.command in {"improvement-plan", "improvement-apply"}:
            from guizhi.application import improvement

            workspace = Workspace(args.workspace)
            if args.command == "improvement-plan":
                result = improvement.prepare(workspace, json.loads(args.request.read_text(encoding="utf-8-sig")))
            else:
                result = improvement.apply(workspace, args.id, json.loads(args.proposal.read_text(encoding="utf-8-sig")))
            print(json.dumps(result))
            return 0
        if args.command in {"diagnose-run", "replay-run", "diagnose-dispatch"}:
            from guizhi.application.diagnostics import diagnose_dispatch, diagnose_run, replay

            workspace = Workspace(args.workspace)
            if args.command == "diagnose-dispatch":
                result = diagnose_dispatch(workspace, args.dispatch_id)
            else:
                assertion = json.loads(args.assertion.read_text(encoding="utf-8-sig")) if args.assertion else None
                result = diagnose_run(workspace, args.run_id, assertion)
                if args.command == "replay-run":
                    result = replay(workspace, result, seconds=args.seconds)
            print(json.dumps(result))
            return 0
        if args.command in {"platform-contract", "platform-handoff"}:
            from guizhi.application.platform import contract, prepare

            result = (contract() if args.command == "platform-contract"
                      else prepare(args.workspace, json.loads(args.request.read_text(encoding="utf-8-sig"))))
            print(json.dumps(result))
            return 0
        if args.command in {"arcgis-stage", "arcgis-capture", "arcgis-revision", "arcgis-notify", "arcgis-view"}:
            from guizhi.infrastructure import arcgis_live

            if args.command == "arcgis-view":
                result = arcgis_live.view(args.workspace, args.bridge)
            elif args.command == "arcgis-notify":
                result = arcgis_live.notify(args.pid, args.window)
            elif args.command == "arcgis-revision":
                result = {"revision": arcgis_live.revision(args.database)}
            else:
                payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
                result = (arcgis_live.prepare(args.workspace, args.bridge, payload) if args.command == "arcgis-stage"
                          else arcgis_live.capture(args.workspace, payload))
            print(json.dumps(result))
            return 0
        if args.command == "_dispatch-replay":
            from guizhi.application.dispatch import replay

            print(json.dumps(replay(args.workspace, args.request)))
            return 0
        if args.command == "capabilities":
            from dataclasses import asdict
            from guizhi.application.skills import OPERATIONS

            print(json.dumps({name: asdict(spec) for name, spec in OPERATIONS.items()}))
            return 0
        if args.command == "capability-show":
            from guizhi.infrastructure.operations.runner import capability

            print(json.dumps(capability(args.operation)))
            return 0
        if args.command == "execute":
            result = run_operation(args.workspace, json.loads(args.request.read_text(encoding="utf-8-sig")))
            print(json.dumps(result))
            return {"succeeded": 0, "cancelled": 130}.get(result["status"], 1)
        if args.command in {"skills", "skill-show", "skill-preview", "skill-trial", "skill-publish", "workflow"}:
            from guizhi.application import skills

            workspace = Workspace(args.workspace, create=True)
            if args.command == "skills":
                result = skills.summaries(workspace)
            elif args.command == "skill-show":
                result = skills.get_skill(workspace, args.reference)
            elif args.command == "skill-preview":
                result = skills.parse_skill(args.request.read_text(encoding="utf-8-sig")).model_dump(mode="json")
            else:
                payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
                handler = {"skill-publish": skills.publish, "skill-trial": skills.trial, "workflow": skills.execute}[args.command]
                result = handler(workspace.root, **payload)
            print(json.dumps(result))
            if args.command == "workflow":
                return {"succeeded": 0, "cancelled": 130}.get(result["status"], 1)
            return 0
        if args.command in {"imagery-models", "imagery-model-add", "imagery-model-activate"}:
            from guizhi.infrastructure import imagery_resources

            workspace = Workspace(args.workspace, create=True)
            if args.command == "imagery-models":
                result = imagery_resources.catalog(workspace)
            elif args.command == "imagery-model-add":
                result = imagery_resources.install(workspace, args.model_file, args.profile_file)
            else:
                result = imagery_resources.activate(workspace, args.name, args.version, args.state == "enabled")
            print(json.dumps(result))
            return 0
        if args.command == "export-shp":
            from guizhi.infrastructure.export import shapefile

            print(json.dumps(shapefile(Workspace(args.workspace), args.run_id, args.kind, args.output)))
            return 0
        if args.command in {"layers", "material-describe", "material-intake"}:
            from guizhi.infrastructure.materials import describe, intake

            if args.command == "material-intake":
                payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
                result = intake(Workspace(args.workspace, create=True), **payload)
            else:
                result = describe(args.file)
                if args.command == "layers":
                    result = result["layers"]
            print(json.dumps(result))
            return 0
        if args.command.startswith("crs-"):
            from guizhi.infrastructure.crs_catalog import catalog, get_plan, remove_definition, rename

            workspace = Workspace(args.workspace, create=True)
            if args.command == "crs-list":
                result = catalog(workspace)
            elif args.command == "crs-show":
                result = get_plan(workspace, args.reference)
            elif args.command == "crs-rename":
                result = rename(workspace, **json.loads(args.request.read_text(encoding="utf-8-sig")))
            elif args.command == "crs-remove":
                result = remove_definition(workspace, args.reference)
            elif args.command == "crs-solve":
                from guizhi.infrastructure.crs_solve import solve

                result = solve(workspace, json.loads(args.request.read_text(encoding="utf-8-sig")))
            else:
                from guizhi.infrastructure.coordinates import import_definition, import_plan

                result = (import_plan(workspace, args.file) if args.command == "crs-import"
                          else import_definition(workspace, args.file, args.layer))
            print(json.dumps(result))
            return 0
        if args.command in {"run", "check"}:
            checking = args.command == "check"
            result = run_vector(args.workspace, args.input, args.layer, args.object_id,
                                operation="control_check" if checking else "vector_inventory",
                                control=(args.control, args.control_layer) if checking else None,
                                project_plan=args.project_plan if checking else None,
                                control_plan=args.control_plan if checking else None)
            print(json.dumps(result))
            return {"succeeded": 0, "cancelled": 130}.get(result["status"], 1)
        workspace = Workspace(args.workspace)
        if args.command == "_worker":
            result = worker(workspace, args.run_id)
        else:
            result = inspect(workspace, args.run_id, cancel=args.command == "cancel")
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
