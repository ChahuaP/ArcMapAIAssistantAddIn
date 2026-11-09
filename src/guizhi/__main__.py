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
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            from dataclasses import asdict
            from guizhi.domain.capabilities import CAPABILITIES

            print(json.dumps({name: asdict(spec) for name, spec in CAPABILITIES.items()}))
            return 0
        if args.command == "execute":
            result = run_operation(args.workspace, json.loads(args.request.read_text(encoding="utf-8-sig")))
            print(json.dumps(result))
            return {"succeeded": 0, "cancelled": 130}.get(result["status"], 1)
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
