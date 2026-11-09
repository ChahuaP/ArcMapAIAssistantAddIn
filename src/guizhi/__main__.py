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
    parser.add_argument("--workspace", type=Path, required=True)
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
    commands.add_parser("crs-list")
    commands.add_parser("crs-import").add_argument("file", type=Path)
    commands.add_parser("crs-define", help="Import the coordinate definition of a Shapefile").add_argument(
        "file", type=Path, help=".shp with its matching .prj in the same folder",
    )
    commands.add_parser("crs-show").add_argument("reference")
    commands.add_parser("execute", help="Run a registered operation from a JSON request").add_argument("request", type=Path)
    commands.add_parser("capabilities", help="List registered independent operations")
    commands.add_parser("capability-show").add_argument("operation")
    commands.add_parser("skills", help="List active skills")
    commands.add_parser("skill-show").add_argument("reference")
    commands.add_parser("skill-preview").add_argument("request", type=Path)
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
    host = commands.add_parser("arcmap", help="Exchange a saved MXD with a local ArcMap 10.2 bridge")
    host.add_argument("bridge", type=Path)
    host.add_argument("request", type=Path)
    commands.add_parser("_dispatch-replay").add_argument("request", type=Path)
    args = parser.parse_args()
    try:
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
        if args.command == "arcmap":
            from guizhi.infrastructure.arcmap import exchange

            print(json.dumps(exchange(args.workspace, args.bridge, json.loads(args.request.read_text(encoding="utf-8-sig")))))
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
        if args.command in {"skills", "skill-show", "skill-preview", "skill-publish", "workflow"}:
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
                handler = skills.publish if args.command == "skill-publish" else skills.execute
                result = handler(workspace.root, **payload)
            print(json.dumps(result))
            if args.command == "workflow":
                return {"succeeded": 0, "cancelled": 130}.get(result["status"], 1)
            return 0
        if args.command == "layers":
            import pyogrio

            if not args.file.is_file() or args.file.suffix.lower() != ".gpkg":
                raise ValueError("请选择 GeoPackage 文件")
            print(json.dumps([name for name, geometry in pyogrio.list_layers(args.file) if geometry]))
            return 0
        if args.command.startswith("crs-"):
            from guizhi.infrastructure.crs_catalog import catalog, get_plan

            workspace = Workspace(args.workspace, create=True)
            if args.command == "crs-list":
                result = catalog(workspace)
            elif args.command == "crs-show":
                result = get_plan(workspace, args.reference)
            else:
                from guizhi.infrastructure.coordinates import import_definition, import_plan

                importer = import_plan if args.command == "crs-import" else import_definition
                result = importer(workspace, args.file)
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
