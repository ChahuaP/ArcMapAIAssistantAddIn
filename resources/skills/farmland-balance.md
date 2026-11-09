---
id: farmland-balance
title: 永农调整与占补平衡
aliases: [补划测算, 永农占补]
materials:
  - {id: removed, label: 调出范围, geometry: polygon, coordinate: projected_meters}
  - {id: replacement, label: 补划范围, geometry: polygon, coordinate: projected_meters}
  - {id: quality, label: 补划质量分区, geometry: polygon, coordinate: projected_meters}
parameters:
  quality_field: 质量指标字段
  minimum_quality: 指标合格下限
  replacement_ratio: 补划与调出比例下限
  basis: 指标和比例的来源及版本
steps:
  - id: balance
    operation: governance.farmland_balance
    inputs: [material.removed, material.replacement, material.quality]
    parameters:
      quality_field: $param.quality_field
      minimum_quality: $param.minimum_quality
      replacement_ratio: $param.replacement_ratio
      basis: $param.basis
evidence:
  - {step: balance, kind: balance}
  - {step: balance, kind: table}
  - {step: balance, kind: report}
---
按本次提供的质量与面积口径测算补划方案。
