---
id: shared-delivery
title: 数据套合与共享交付
aliases: [范围裁切交付, 共享数据包]
materials:
  - {id: vectors, label: 共享矢量}
  - {id: image, label: 共享栅格, kind: raster}
  - {id: scope, label: 授权范围, geometry: polygon}
  - {id: attachments, label: 授权附件, kind: document, minimum: 0, maximum: 12}
parameters:
  fields: 授权交付的矢量字段列表
  required: 要求随包交付的附件格式列表
steps:
  - id: vectors
    operation: delivery.clip_vector
    inputs: [material.vectors, material.scope]
    parameters: {fields: $param.fields}
  - id: image
    operation: raster.clip
    inputs: [material.image, material.scope]
  - id: attachments
    operation: delivery.check_attachments
    inputs: [material.attachments]
    parameters: {required: $param.required}
  - id: bundle
    operation: delivery.bundle
    parameters: {vector_run: $run.vectors, raster_run: $run.image, attachment_run: $run.attachments}
evidence:
  - {step: bundle, kind: package}
  - {step: bundle, kind: report}
---
按同一授权范围裁切数据并发布带校验清单的共享包。
