"""Report a missing runtime before importing native business dependencies."""
from importlib import invalidate_caches
from importlib.util import find_spec
from pathlib import Path
import sys

from guizhi.domain.diagnostics import Fault

COMPONENTS = {
    "ocr": ("rapidocr_onnxruntime", "文档 OCR"),
    "raster": ("rasterio", "栅格处理"),
    "imagery": ("onnxruntime", "专业影像识别"),
}


def require(kind):
    module, title = COMPONENTS[kind]
    # A resident material reader or MCP process may have started before installation.
    invalidate_caches()
    paths = [path for value in sys.path if (path := Path(value)).parent.name == "guizhi-python-components"]
    installed = not paths or any((path / "component.json").is_file()
                                 for path in paths if path.name.startswith(kind + "-"))
    if not installed or find_spec(module) is None:
        raise Fault("environment", "component_required", f"请先在「设置 → 功能模块」安装{title}组件。",
                    component=kind)
