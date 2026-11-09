"""Active independent operations. Identifiers are canonical, including at the tool interface."""
from dataclasses import dataclass
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Hash = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


@dataclass(frozen=True)
class Capability:
    title: str
    minimum: int = 1
    maximum: int = 1
    edit: bool = False
    version: str = "1.0.0"


CAPABILITIES = {
    "data.copy": Capability("复制要素"),
    "data.merge": Capability("合并数据", 2, 32),
    "data.append": Capability("追加要素", 2, 32, True),
    "data.describe": Capability("描述数据"),
    "data.list_fields": Capability("列出字段"),
    "data.selection_count": Capability("统计已选要素"),
    "data.select_by_attribute": Capability("按属性选择"),
    "data.select_by_location": Capability("按位置选择", 2, 2),
    "data.clear_selection": Capability("清除选择"),
    "data.export_selection": Capability("导出选择"),
    "vector.buffer": Capability("缓冲分析"),
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
    "table.update_rows": Capability("更新属性", edit=True),
    "table.delete_rows": Capability("删除要素", edit=True),
    "crs.assign": Capability("定义已知坐标系", edit=True),
    "crs.transform": Capability("按已验证方案转换坐标"),
    "geometry.create_dataset": Capability("新建空图层", 0, 1),
    "geometry.create_points": Capability("创建点", 0, 1),
    "geometry.append_points": Capability("追加点", edit=True),
    "geometry.create_lines": Capability("创建线", 0, 1),
    "geometry.append_lines": Capability("追加线", edit=True),
    "geometry.create_polygons": Capability("创建面", 0, 1),
    "geometry.append_polygons": Capability("追加面", edit=True),
    "geometry.create_rectangle": Capability("创建矩形", 0, 1),
    "geometry.create_regular_polygons": Capability("创建正多边形", 0, 1),
    "geometry.append_regular_polygons": Capability("追加正多边形", edit=True),
    "geometry.create_stars": Capability("创建星形", 0, 1),
    "geometry.append_stars": Capability("追加星形", edit=True),
    "geometry.explode": Capability("拆分多部件"),
    "geometry.repair": Capability("修复几何", edit=True),
}


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    layer: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    revision: Hash | None = None


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    inputs: list[Material] = Field(default_factory=list, max_length=32)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
