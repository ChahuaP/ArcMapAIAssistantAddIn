---
id: control-check
title: 管控线核查
aliases: [管控线检查, 叠加核查, 覆盖率]
materials:
  - {id: project, label: 项目范围, geometry: polygon, minimum: 1, maximum: 1}
  - {id: control, label: 管控范围, geometry: polygon, minimum: 1, maximum: 1}
steps:
  - id: check
    operation: control_check
    inputs: [material.project, material.control]
evidence:
  - {step: check, kind: map}
  - {step: check, kind: report}
---

# 管控线核查

提交项目范围与管控范围，取得重叠面积、覆盖率和空间关系。采用已经定义的米制分析坐标系或绑定已验证的转换方案。成果包括地图、报告和可追溯证据。
