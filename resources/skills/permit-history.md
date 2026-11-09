---
id: permit-history
title: 用地手续与历史地类
aliases: [用地手续核查, 用地历史分析]
materials:
  - {id: project, label: 项目范围, geometry: polygon, coordinate: projected_meters}
  - {id: permits, label: 已登记手续范围, geometry: polygon, coordinate: projected_meters}
  - {id: from, label: 前期地类, geometry: polygon, coordinate: projected_meters}
  - {id: to, label: 后期地类, geometry: polygon, coordinate: projected_meters}
parameters:
  from_year: 前期年份
  to_year: 后期年份
  field: 地类编码字段
  classifications: 逐年分类对照表
  as_of: 核查日期
  intended_use: 待核查用途
  approved_status: 适用状态值
  approval_source: 审批数据来源与版本
steps:
  - id: from
    operation: vector.clip
    inputs: [material.from, material.project]
  - id: to
    operation: vector.clip
    inputs: [material.to, material.project]
  - id: change
    operation: annual.compare
    inputs: [step.from, step.to]
    parameters: {from_year: $param.from_year, to_year: $param.to_year, field: $param.field, classifications: $param.classifications}
  - id: permits
    operation: governance.permit_history
    inputs: [material.project, material.permits]
    parameters: {as_of: $param.as_of, intended_use: $param.intended_use, approved_status: $param.approved_status, approval_source: $param.approval_source, history_run: $run.change}
evidence:
  - {step: permits, kind: permit_history}
  - {step: permits, kind: map}
  - {step: permits, kind: report}
---
按来源、日期、状态和用途核对已有手续的覆盖，并与项目范围内的年度变化同批发布。
