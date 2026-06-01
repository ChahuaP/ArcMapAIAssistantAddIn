# GeoPilot Workflow Examples

Use these as patterns only after checking `/api/capabilities`. Keep operation ids and argument names exactly as the capability schema reports.

## Add Shapefile

```json
{
  "action": "execute",
  "summary": "加载 nanjing.shp。",
  "steps": [
    {
      "id": "step_1",
      "operation": "layer.add_layer",
      "arguments": {"path": "D:/Data/shapefile/nanjing.shp"},
      "reason": "把用户指定的 Shapefile 加入当前地图。"
    }
  ]
}
```

## Select By Attribute

```json
{
  "action": "execute",
  "summary": "选择 NAME 包含南京的要素。",
  "steps": [
    {
      "id": "step_1",
      "operation": "selection.select_by_attribute",
      "arguments": {
        "layer": "nanjing",
        "where": {"field": "NAME", "op": "like", "value": "%南京%"},
        "selection_type": "NEW_SELECTION"
      },
      "reason": "用结构化 where 表达文本包含条件。"
    }
  ]
}
```

## Buffer Output

```json
{
  "action": "execute",
  "summary": "对 roads 生成 100 米缓冲区。",
  "steps": [
    {
      "id": "step_1",
      "operation": "analysis.buffer",
      "arguments": {
        "input_layer": "roads",
        "distance": "100 Meters",
        "output_name": "roads_buffer_100m"
      },
      "reason": "生成新的缓冲区要素类。"
    }
  ]
}
```

## Intersect

```json
{
  "action": "execute",
  "summary": "执行 roads 和 boundary 的相交分析。",
  "steps": [
    {
      "id": "step_1",
      "operation": "analysis.intersect",
      "arguments": {
        "input_layers": ["roads", "boundary"],
        "output_name": "roads_boundary_intersect"
      },
      "reason": "用两个输入图层生成相交结果。"
    }
  ]
}
```

## Split Export To Shapefiles

```json
{
  "action": "execute",
  "summary": "按 NAME 字段拆分导出 Shapefile。",
  "steps": [
    {
      "id": "step_1",
      "operation": "export.split_by_field",
      "arguments": {
        "layer": "nanjing",
        "field": "NAME",
        "output_name": "nanjing_by_name",
        "output_format": "shp",
        "max_outputs": 200
      },
      "reason": "按字段唯一值分别生成输出文件。"
    }
  ]
}
```

## Create A Five-Point Star

```json
{
  "action": "execute",
  "summary": "创建五角星面要素。",
  "steps": [
    {
      "id": "step_1",
      "operation": "edit.create_star_polygon",
      "arguments": {
        "center_x": 118.78,
        "center_y": 32.04,
        "outer_radius": 0.01,
        "outer_radius_unit": "degrees",
        "point_count": 5,
        "wkid": 4326,
        "output_name": "star_feature"
      },
      "reason": "按中心点和半径创建新的五角星 polygon feature。"
    }
  ]
}
```

## Create Empty WGS84 Polygon Layer

```json
{
  "action": "execute",
  "summary": "创建一个空的 WGS84 面图层。",
  "steps": [
    {
      "id": "step_1",
      "operation": "edit.create_empty_feature_layer",
      "arguments": {
        "geometry_type": "polygon",
        "wkid": 4326,
        "output_name": "polygon_layer"
      },
      "reason": "用户要求创建空面图层，没有要求创建具体 feature。"
    }
  ]
}
```

## Create Rectangle From Corners

```json
{
  "action": "execute",
  "summary": "根据左上角和右下角创建 WGS84 矩形面。",
  "steps": [
    {
      "id": "step_1",
      "operation": "edit.create_rectangle_polygon",
      "arguments": {
        "left": 120,
        "top": 30,
        "right": 125,
        "bottom": 20,
        "wkid": 4326,
        "output_name": "rectangle_120_30_125_20"
      },
      "reason": "用户给出左上角和右下角，直接创建一个矩形 polygon feature。"
    }
  ]
}
```

## Create Multiple Stars In One New Layer

```json
{
  "action": "execute",
  "summary": "创建多个五角星到同一个面图层。",
  "steps": [
    {
      "id": "step_1",
      "operation": "edit.create_star_polygon",
      "arguments": {
        "features": [
          {"center_x": 118.78, "center_y": 32.04, "name": "star_1"},
          {"center_x": 118.79, "center_y": 32.05, "name": "star_2"}
        ],
        "outer_radius": 0.01,
        "outer_radius_unit": "degrees",
        "point_count": 5,
        "wkid": 4326,
        "output_name": "stars"
      },
      "reason": "用户要一个输出图层，因此把多个五角星作为多个 feature 写入同一个新 polygon 图层。"
    }
  ]
}
```

## Append Stars To An Existing Layer

```json
{
  "action": "execute",
  "summary": "向 stars 图层追加两个五角星。",
  "steps": [
    {
      "id": "step_1",
      "operation": "edit.append_star_polygons",
      "arguments": {
        "target_layer": "stars",
        "features": [
          {"center_x": 118.78, "center_y": 32.04, "name": "star_1"},
          {"center_x": 118.79, "center_y": 32.05, "name": "star_2"}
        ],
        "outer_radius": 0.01,
        "outer_radius_unit": "degrees",
        "point_count": 5
      },
      "reason": "用户明确要求写入已有 stars 图层，因此直接追加 feature；该步骤需要 edit authorization。"
    }
  ]
}
```

## Copy And Repair Data

Copying writes a new dataset:

```json
{
  "action": "execute",
  "summary": "复制 parcels 图层。",
  "steps": [
    {
      "id": "step_1",
      "operation": "data.copy_features",
      "arguments": {"input_layer": "parcels", "output_name": "parcels_copy"},
      "reason": "生成一份新的要素副本。"
    }
  ]
}
```

Repairing geometry edits the source dataset and requires `allow_edits`:

```json
{
  "action": "execute",
  "summary": "修复 parcels 几何。",
  "steps": [
    {
      "id": "step_1",
      "operation": "data.repair_geometry",
      "arguments": {"layer": "parcels", "delete_null": "DELETE_NULL"},
      "reason": "直接修复源数据几何错误。"
    }
  ]
}
```

## Layout Title And PDF

```json
{
  "action": "execute",
  "summary": "更新版面标题并导出 PDF。",
  "steps": [
    {
      "id": "step_1",
      "operation": "layout.set_text",
      "arguments": {"element_name": "Title", "text": "南京用地现状图"},
      "reason": "修改已有标题元素。"
    },
    {
      "id": "step_2",
      "operation": "layout.export_pdf",
      "arguments": {"output_name": "nanjing_layout", "resolution": 300},
      "reason": "导出当前版面为 PDF。"
    }
  ]
}
```

## First-Run Agent Check

Run this sequence before assuming ArcMap is ready:

```text
scripts/geopilot_cli.py health
scripts/geopilot_cli.py doctor
scripts/geopilot_cli.py arcmap-list
scripts/geopilot_cli.py arcmap-select --hwnd <hwnd>   # only when multiple ArcMap windows exist
scripts/geopilot_cli.py arcmap-sync
scripts/geopilot_cli.py capabilities
scripts/geopilot_cli.py capabilities --detail   # when exact argument schemas are needed
```
