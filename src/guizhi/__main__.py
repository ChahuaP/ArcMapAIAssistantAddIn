"""Source entry point for the Guizhi vector task runner."""
import argparse
import json
from pathlib import Path
import sys

# The embedded interpreter is deliberately isolated from global packages and PYTHONPATH.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guizhi.application.tasks import inspect, product_version, run_vector, worker
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
    for command in ("inspect", "cancel", "_worker"):
        commands.add_parser(command).add_argument("run_id")
    args = parser.parse_args()
    try:
        if args.command == "run":
            result = run_vector(args.workspace, args.input, args.layer, args.object_id)
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
