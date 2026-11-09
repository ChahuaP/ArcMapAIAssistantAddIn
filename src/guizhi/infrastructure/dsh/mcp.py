"""Expose the independent vector operation on the DSH stdio transport."""
import argparse
import json
from pathlib import Path
import sys
from threading import Lock

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from guizhi.application.tasks import run_vector
from guizhi.domain.operations import OPERATIONS
from guizhi.infrastructure.workspace import external_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dispatch", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--layer")
    parser.add_argument("--control", type=Path)
    parser.add_argument("--control-layer")
    args = parser.parse_args()
    workspace = external_root(args.workspace)
    dispatch = args.dispatch.resolve(strict=True)
    if dispatch.parent != workspace / "dispatch":
        raise ValueError("Dispatch must belong to the selected workspace")

    server = FastMCP("Guizhi")
    mutex = Lock()
    receipt = dispatch / "operation.json"
    # Keep the claim after an interrupted call so rediscovery cannot repeat a side effect.
    claim = dispatch / "operation.claim"
    cancelled = dispatch / "cancelled"

    def execute(operation):
        with mutex:
            if receipt.exists():
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                return saved if saved["operation"] == operation else {"status": "operation_already_selected"}
            if cancelled.exists():
                return {"status": "cancelled"}
            try:
                claim.touch(exist_ok=False)
            except FileExistsError:
                return {"status": "interrupted", "message": "Operation was already started"}
            result = {"operation": operation}
            control = (args.control, args.control_layer) if args.control and args.control_layer else None
            if not args.input or not args.layer or (operation == "control_check" and control is None):
                result.update(status="missing_materials", required=OPERATIONS[operation].materials)
            else:
                try:
                    record = run_vector(workspace, args.input, args.layer, None, cancelled.exists,
                                        operation=operation, control=control)
                    result.update(status=record["status"], run_id=record["id"])
                    if record["status"] == "succeeded":
                        result["statistics"] = record["result"]["statistics"]
                    elif record["error"]:
                        result["error"] = record["error"]
                except (ValueError, OSError) as exc:
                    result.update(status="invalid_materials", error=type(exc).__name__)
            pending = receipt.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(receipt)
            return result

    @server.tool
    def vector_inventory() -> dict:
        """统计绑定的 GeoPackage 项目图层要素数及几何类型。"""
        return execute("vector_inventory")

    @server.tool
    def control_check() -> dict:
        """核查绑定的项目范围和管控范围，统计重叠面积、覆盖比例和空间关系，生成报告。"""
        return execute("control_check")

    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
