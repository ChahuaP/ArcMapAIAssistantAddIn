---
id: polygon-buffer
title: 范围缓冲
aliases: [缓冲范围]
materials:
  - id: project
    label: 项目范围
    geometry: polygon
    coordinate: projected_meters
    minimum: 1
    maximum: 1
steps:
  - id: buffer
    operation: vector.buffer
    inputs: [material.project]
    parameters:
      distance: 100
      unit: meters
rules:
  - {step: buffer, statistic: feature_count, compare: ge, value: 1}
evidence:
  - {step: buffer, kind: export}
---

# 范围缓冲

在已知投影坐标系下为项目范围生成 100 米缓冲。修改标题、材料角色、操作、参数和交付说明后，用代表性数据验证并发布。可在业务面板查看可用操作。多材料角色可以通过 minimum 和 maximum 指定数量范围。后一步用 step.步骤编号引用前一步产出的数据。

coordinate 可填 any、defined、projected_meters。rules 验证步骤产出的统计量，支持 eq、ge、le。evidence 声明必须生成的成果类型，例如 export、map、report 或 table。
