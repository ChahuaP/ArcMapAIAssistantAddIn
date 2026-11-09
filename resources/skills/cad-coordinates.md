---
id: cad-coordinates
title: CAD 坐标整理
aliases: [DXF 图层整理, CAD 数据转换]
materials:
  - {id: drawing, label: DXF 图纸, kind: cad}
parameters:
  source_crs: 已确认的图纸原坐标系
  cad_layer: 目标图层名，全部图层时填空值
  plan: 已核验转换方案版本，坐标不变时填空值
  force_2d: 是否明确转成二维
steps:
  - id: converted
    operation: cad.convert
    inputs: [material.drawing]
    parameters: {source_crs: $param.source_crs, layer: $param.cad_layer, plan: $param.plan, force_2d: $param.force_2d}
evidence:
  - {step: converted, kind: export}
  - {step: converted, kind: report}
---
按明确指定的坐标依据导入 DXF，成果包含地理数据及图层清单。
