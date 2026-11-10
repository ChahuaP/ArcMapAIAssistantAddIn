---
id: imagery-change
title: 影像地类与变化核查
aliases: [识别地类, 提取地类边界, 两期影像变化, 变更调查影像核查]
materials:
  - {id: from, label: 前期多波段影像, kind: raster, coordinate: projected_meters}
  - {id: to, label: 后期多波段影像, kind: raster, coordinate: projected_meters}
  - {id: model, label: 地类识别模型, kind: model, resource: imagery-model}
parameters:
  region: 调查区域
  sensor: 传感器标识
  from_date: 前期影像日期
  to_date: 后期影像日期
steps:
  - id: before
    operation: imagery.classify
    inputs: [material.from, material.model]
    parameters: {model: $param.profile, observed_on: $param.from_date, region: $param.region, sensor: $param.sensor}
  - id: after
    operation: imagery.classify
    inputs: [material.to, material.model]
    parameters: {model: $param.profile, observed_on: $param.to_date, region: $param.region, sensor: $param.sensor}
  - id: compare
    operation: imagery.change
    inputs: [step.before, step.after]
evidence:
  - {step: before, kind: classification}
  - {step: before, kind: boundaries}
  - {step: after, kind: classification}
  - {step: after, kind: boundaries}
  - {step: compare, kind: boundaries}
  - {step: compare, kind: export}
  - {step: compare, kind: table}
  - {step: compare, kind: report}
---
在已声明的模型适用范围内分别识别两期影像，交付地类边界、变化图层和变化矩阵，单独统计未知与缺测区域。
