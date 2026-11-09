"""Expose the independent vector operation on the DSH stdio transport."""
import argparse
import json
from pathlib import Path
import sys
from threading import Lock

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from guizhi.application.tasks import run_vector
from guizhi.infrastructure.workspace import external_root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--dispatch", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--layer")
    args = parser.parse_args()
    workspace = external_root(args.workspace)
    dispatch = args.dispatch.resolve(strict=True)
    if dispatch.parent != workspace / "dispatch":
        raise ValueError("Dispatch must belong to the selected workspace")

    server = FastMCP("Guizhi")
    mutex = Lock()
    receipt = dispatch / "inventory.json"
    # Keep the claim after an interrupted call so rediscovery cannot repeat a side effect.
    claim = dispatch / "inventory.claim"
    cancelled = dispatch / "cancelled"

    @server.tool
    def vector_inventory() -> dict:
        """统计调用方绑定的 GeoPackage 图层要素总数及几何类型，验证并保存导出成果。"""
        if args.input is None or not args.layer:
            return {"status": "missing_materials", "required": ["GeoPackage", "layer"]}
        with mutex:
            if receipt.exists():
                return json.loads(receipt.read_text(encoding="utf-8"))
            if cancelled.exists():
                return {"status": "cancelled"}
            try:
                claim.touch(exist_ok=False)
            except FileExistsError:
                return {"status": "interrupted", "message": "Operation was already started"}
            try:
                record = run_vector(workspace, args.input, args.layer, None, cancelled.exists)
                result = {"status": record["status"], "run_id": record["id"]}
                if record["status"] == "succeeded":
                    result["statistics"] = record["result"]["statistics"]
            except (ValueError, OSError) as exc:
                result = {"status": "invalid_materials", "error": type(exc).__name__}
            pending = receipt.with_suffix(".pending")
            pending.write_text(json.dumps(result), encoding="utf-8")
            pending.replace(receipt)
            return result

    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
