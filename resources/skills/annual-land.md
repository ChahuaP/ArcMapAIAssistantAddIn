---
id: annual-land
title: 地类面积与年度变化
aliases: [年度地类统计, 历年变化]
materials:
  - {id: from, label: 前期地类, geometry: polygon, coordinate: projected_meters}
  - {id: to, label: 后期地类, geometry: polygon, coordinate: projected_meters}
parameters:
  from_year: 前期年份
  to_year: 后期年份
  field: 地类原始编码字段
  classifications: 按年份分别提供的地类对照表
steps:
  - id: summary
    operation: annual.summarize
    inputs: [material.from, material.to]
    parameters: {years: [$param.from_year, $param.to_year], field: $param.field, classifications: $param.classifications}
  - id: change
    operation: annual.compare
    inputs: [material.from, material.to]
    parameters: {from_year: $param.from_year, to_year: $param.to_year, field: $param.field, classifications: $param.classifications}
  - id: report
    operation: annual.report
    parameters: {summary_run: $run.summary, changes_run: $run.change}
evidence:
  - {step: change, kind: export}
  - {step: report, kind: table}
  - {step: report, kind: report}
---
按明确年份与分类映射发布统计表、变化图层和报告。
