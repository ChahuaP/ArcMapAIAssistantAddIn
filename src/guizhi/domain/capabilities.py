"""Active independent operations. Identifiers are canonical, including at the tool interface."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Hash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]

MATERIAL_FORMATS = {
    ".gdb": "vector", ".gpkg": "vector", ".shp": "vector", ".geojson": "vector", ".tif": "raster", ".tiff": "raster",
    ".img": "raster", ".dxf": "cad", ".pdf": "document", ".docx": "document", ".xlsx": "document",
    ".csv": "document", ".txt": "document", ".onnx": "model",
}


def material_path_exists(path):
    return path.is_dir() if path.suffix.lower() == ".gdb" else path.is_file()


def material_kind(file):
    suffix = Path(file).suffix.lower()
    if suffix == ".dwg":
        raise ValueError("当前运行组件无法读取 DWG，请在 CAD 中另存为 DXF 后添加")
    if suffix in {".doc", ".xls"}:
        raise ValueError("请将旧版 Office 文件另存为 DOCX、XLSX 或 PDF 后添加")
    if suffix not in MATERIAL_FORMATS:
        raise ValueError(f"不支持的材料格式 {suffix}")
    return MATERIAL_FORMATS[suffix]


@dataclass(frozen=True)
class Capability:
    title: str
    minimum: int = 1
    maximum: int = 1
    edit: bool = False
    version: str = "1.0.0"
    kinds: tuple[str, ...] = ("vector",)


CAPABILITIES = {
    "xy.inspect": Capability("查看坐标表字段", kinds=("document",)),
    "xy.points": Capability("添加 XY 数据", kinds=("document",)),
    "data.copy": Capability("复制要素"),
    "data.merge": Capability("合并数据", 2, 32),
    "data.append": Capability("追加要素", 2, 32, True),
    "data.describe": Capability("描述数据", version="1.1.0"),
    "data.list_fields": Capability("列出字段", version="1.0.1"),
    "data.selection_count": Capability("统计已选要素"),
    "data.select_by_attribute": Capability("按属性选择", version="1.1.0"),
    "data.select_by_location": Capability("按位置选择", 2, 2, version="1.1.0"),
    "data.clear_selection": Capability("清除选择"),
    "data.export_selection": Capability("导出选择", version="1.1.0"),
    "vector.area": Capability("汇总面积", version="1.2.0"),
    "vector.buffer": Capability("缓冲分析", version="1.1.0"),
    "vector.clip": Capability("裁切", 2, 2),
    "vector.intersect": Capability("相交", 2, 32),
    "vector.dissolve": Capability("融合"),
    "vector.spatial_join": Capability("空间连接", 2, 2),
    "vector.erase": Capability("擦除", 2, 2),
    "vector.identity": Capability("标识叠加", 2, 2),
    "vector.union": Capability("联合", 2, 32),
    "vector.symmetric_difference": Capability("对称差集", 2, 2),
    "vector.update_overlay": Capability("更新叠加", 2, 2),
    "table.add_coordinates": Capability("写入点坐标"),
    "table.add_field": Capability("添加字段", edit=True),
    "table.delete_field": Capability("删除字段", edit=True),
    "table.summarize": Capability("分组汇总", version="1.1.0"),
    "table.lookup": Capability("查询匹配要素的字段值", version="1.1.0"),
    "table.calculate_field": Capability("计算字段", edit=True, version="1.3.0"),
    "table.calculate_geometry": Capability("计算几何", edit=True, version="1.3.0"),
    "table.edit_cell": Capability("编辑属性值", edit=True),
    "table.update_rows": Capability("更新属性", edit=True, version="1.0.1"),
    "table.delete_rows": Capability("删除要素", edit=True, version="1.0.1"),
    "crs.assign": Capability("定义坐标系", edit=True, version="1.2.0"),
    "crs.assign_from": Capability("沿用参照图层坐标系", 2, 2, edit=True, version="1.1.0"),
    "crs.transform": Capability("按已验证方案转换坐标"),
    "crs.reproject": Capability("投影转换", version="1.2.0"),
    "edit.changes": Capability("保存图层编辑", edit=True, version="1.1.0"),
    "edit.transfer": Capability("在图层间复制或剪切要素", 2, 2, edit=True, version="1.2.0"),
    "edit.transfer_plan": Capability("预备图层间粘贴", 2, 2),
    "geometry.create_dataset": Capability("新建空图层", 0, 1, version="2.0.0"),
    "geometry.create_points": Capability("创建点", 0, 1),
    "geometry.append_points": Capability("追加点", edit=True, version="1.1.0"),
    "geometry.create_lines": Capability("创建线", 0, 1),
    "geometry.append_lines": Capability("追加线", edit=True, version="1.1.0"),
    "geometry.create_polygons": Capability("创建面", 0, 1),
    "geometry.append_polygons": Capability("追加面", edit=True, version="1.1.0"),
    "geometry.create_rectangle": Capability("创建矩形", 0, 1),
    "geometry.create_regular_polygons": Capability("创建正多边形", 0, 1),
    "geometry.append_regular_polygons": Capability("追加正多边形", edit=True, version="1.1.0"),
    "geometry.create_stars": Capability("创建星形", 0, 1),
    "geometry.append_stars": Capability("追加星形", edit=True, version="1.1.0"),
    "geometry.multipart": Capability("合并为多部件", edit=True, version="2.0.0"),
    "geometry.explode": Capability("拆分多部件", edit=True, version="2.0.0"),
    "geometry.repair": Capability("修复几何", edit=True),
    "geometry.check_topology": Capability("拓扑检查"),
    "map_raster.preview": Capability("生成影像预览", kinds=("raster",)),
    "raster.statistics": Capability("栅格信息、波段统计与无数据像元检查", kinds=("raster",)),
    "raster.clip": Capability("按范围裁切栅格", 2, 2, kinds=("raster", "vector")),
    "raster.reproject": Capability("栅格重投影与重采样", version="1.2.0", kinds=("raster",)),
    "raster.assign": Capability("定义影像坐标系", version="1.1.0", kinds=("raster",)),
    "raster.mosaic": Capability("拼接已对齐的栅格", 2, 32, kinds=("raster",)),
    "imagery.classify": Capability("识别多波段影像地类及边界", 2, 2, kinds=("raster", "model")),
    "imagery.change": Capability("对比两期影像地类与变化边界", 2, 2, kinds=("raster",)),
    "cad.inspect": Capability("读取 DXF 版本、图层和几何类型", kinds=("cad",)),
    "cad.convert": Capability("整理 DXF 图层和已知坐标系", kinds=("cad",)),
    "annual.summarize": Capability("汇总年度地类面积", 1, 32),
    "annual.compare": Capability("计算年度地类变化矩阵", 2, 2),
    "annual.report": Capability("导出年度面积与变化报告", 0, 0),
    "delivery.check_attachments": Capability("核查共享交付附件", 0, 32, kinds=("document",)),
    "delivery.clip_vector": Capability("按范围和授权字段裁切共享矢量", 2, 2),
    "delivery.bundle": Capability("生成经范围与附件校验的数据包", 0, 0),
    "governance.farmland_balance": Capability("计算调出、补划和质量平衡", 3, 3),
    "governance.permit_history": Capability("核对用地手续覆盖与年度地类变化", 2, 2),
    "presentation.export_table": Capability("导出属性表"),
    "flow.unique_values": Capability("唯一值"),
    "flow.condition": Capability("查询条件"),
    "flow.calculate": Capability("计算", 0, 0),
    "presentation.map_layout": Capability("创建、调整或导出地图版面", 0, 0, version="1.2.0"),
    "presentation.map_view": Capability("定位图层、显隐、符号颜色、属性表和地图背景", 0, 0),
    "system.health": Capability("查询独立运行状态", 0, 0),
    "evidence.verify": Capability("验证已交付证据", 0, 0),
    "evidence.history": Capability("查询作业历史", 0, 0),
    "semantics.read_documents": Capability("读取审批材料", 1, 32, kinds=("document",)),
    "semantics.approval_case": Capability("关联审批材料、地块与结论"),
    "knowledge.register_catalog": Capability("登记规则、术语和模型适用范围", 0, 0),
    "knowledge.extract_facts": Capability("提取并核对材料记载", 0, 0),
    "knowledge.map_terms": Capability("映射已登记业务术语", 0, 0),
    "knowledge.match_objects": Capability("按编码和地域匹配业务对象", 0, 0),
    "knowledge.find_rules": Capability("查询指定时点的适用规则", 0, 0),
    "knowledge.model_scope": Capability("检查模型适用范围", 0, 0),
    "governance.explain_findings": Capability("生成带依据的业务解释", 0, 0),
    "workflow.execute": Capability("执行已编排任务", 0, 0),
}


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    layer: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    revision: Hash | None = None

    @property
    def kind(self):
        kind = material_kind(self.file)
        if (self.layer is not None) != (kind == "vector"):
            raise ValueError("矢量材料需要指定图层，其他材料使用文件")
        return kind


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    inputs: list[Material] = Field(default_factory=list, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
