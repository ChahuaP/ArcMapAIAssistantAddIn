"""Parameter meanings supplement the JSON schema generated from executor signatures."""
PARAMETERS = {
    "group_field": "用于分组的真实属性字段名，每个唯一值生成一个 KML。",
    "naming": "sequence 按字段值排序后以 1、2、3 命名；group 用分组值命名。",
    "sequence": "在第一列生成从 1 开始的行序号。",
    "total_fields": "需要在最后一行求和的数值字段列表，必须包含在 fields 中。",
    "action": "地图操作种类。", "all_touched": "是否保留与裁切边界接触的所有像元。",
    "approval_source": "审批手续信息来源。", "approved_status": "认定为已批准的状态值。",
    "area_field": "可选的已有面积字段，以平方米计。", "as_of": "用于判定有效性的基准日期。",
    "destination": "current 在地图上更新原图层并保留未选要素；new 将所选要素的处理结果输出到新图层。",
    "assignments": "待更新的字段名与新值。", "attribute_fid": "合并后沿用其属性的要素编号。",
    "basis": "面积与质量核算的业务依据。", "border": "是否绘制图框。",
    "bottom": "下边界的纵坐标，单位与目标坐标系一致。", "cap": "缓冲区端部样式。",
    "changes": "按执行顺序排列的要素编辑指令。", "classifications": "源地类值与统一分类的映射。",
    "codes": "需要匹配的对象编码。", "color": "地图符号或背景的颜色。", "constant": "带类型的常量赋值。",
    "control_plan": "用于管控范围的已登记坐标转换方案编号。", "definition": "目标空间参考定义，如 EPSG 编号或 WKT。",
    "delete_null": "是否删除空几何。", "direction": "字段排序方向。", "dissolve": "是否融合相互重叠的缓冲区。",
    "distance": "缓冲或邻近距离，计量单位由 unit 指定。", "editions": "待登记的规则版本及其来源。",
    "epsg": "新图层的 EPSG 坐标系编号。", "export": "是否请求桌面端导出排版成果。",
    "expression": "用于字段计算的受支持表达式。", "facts": "含材料来源和原文位置的结构化事实。",
    "features": "要生成的要素定义，坐标使用目标图层的空间参考。", "fid": "目标图层中的要素编号。",
    "fids": "所选要素的编号。", "field": "要读取、分组或修改的字段。", "fields": "参与操作的字段名称列表。",
    "fill": "面符号的填充颜色。", "fill_opacity": "面填充的不透明度，0 为透明、100 为不透明。",
    "force_2d": "是否将 CAD 要素降为二维。", "format": "导出文件格式。",
    "frames": "版面各地图框的设置。", "from_year": "前一期数据的年份。", "geometry_type": "新建点、线或面图层。同族单部件和多部件要素可以共存。",
    "intended_use": "拟办理事项的用地用途。", "join": "线转角处的缓冲连接样式。",
    "layer": "操作目标的图层标识。", "left": "左操作数。", "legend": "是否显示图例。",
    "limit": "最多返回的历史记录数。", "matter": "查询规则所针对的业务事项。",
    "method": "重叠部分按融合后面积统计（union）或逐个要素求和（sum）。",
    "minimum_quality": "满足质量要求的最低字段值。", "mode": "操作方式，允许值见枚举。",
    "model": "所用模型或已登记的模型适用范围配置。", "models": "待登记的模型范围和评估信息。",
    "monochrome": "是否采用黑白版面。", "name": "新建要素或图层的名称。", "note": "版面附注。",
    "observed_on": "影像观测日期。", "operand": "参与数值运算的常数。", "operator": "计算运算符。",
    "orientation": "纸张方向。", "output_layer": "是否另存可供后续步骤引用的矢量成果。",
    "overlap": "像元重叠时取优先栅格的规则。", "paper": "版面纸张规格。",
    "parcel_field": "记录地块编号的字段名。", "plan": "已登记并验证的坐标转换方案编号。",
    "positions": "版面元素的位置和尺寸。", "precise": "是否要求精确空间关联。",
    "predicate": "目标要素相对于参照要素的空间关系。", "previous": "同一图层版本的上一选区，仅用于组合选择。",
    "project_code": "业务项目编号。", "project_field": "记录项目编号的字段名。",
    "project_plan": "用于项目范围的已登记坐标转换方案编号。", "property": "待写入字段的几何量。",
    "provider": "模型提供方。", "quality_field": "记录质量等级或数值的字段。",
    "query": "对字段候选值应用的搜索文字。", "region": "业务或模型适用的行政区域。",
    "replacement_ratio": "要求的补划面积与调出面积之比。", "required": "需要满足的必备项。",
    "resampling": "生成目标栅格时的像元插值方法。", "resolution": "目标栅格像元横纵尺寸，单位为目标坐标系单位。",
    "right": "右操作数。", "scale": "是否显示比例尺。", "schema": "追加时严格校验或映射字段结构。",
    "selection": "属于第一个输入图层及相同数据版本的选区。", "sensor": "影像传感器名称。",
    "source_crs": "CAD 源坐标系定义。", "source_field": "参与计算的来源字段。",
    "sql": "只填写 WHERE 后的布尔表达式，如 DLMC = '草地'。与结构化 where 二选一。", "target_crs": "目标空间参考，如 EPSG:4548。",
    "task": "核对模型适用范围的任务种类。", "terms": "需要登记或查询的业务术语。",
    "title": "图名或报告标题。", "to_year": "后一期数据的年份。", "top": "上边界纵坐标，单位与目标坐标系一致。",
    "transitions": "与上一办理案例关联的地块变化。", "unit": "距离、面积或长度的计量单位，见允许值。",
    "update": "是否基于当前版面更新。", "value": "待写入字段的值，类型应匹配字段。",
    "version": "用于检查适用范围的模型版本。", "visible": "目标图层是否可见。",
    "where": "以字段名、比较运算符和值描述的筛选条件。", "x_field": "写入横坐标的字段。",
    "y_field": "写入纵坐标的字段。", "years": "与各输入图层顺序对应的年份。",
}

RUN_INPUTS = {
    "run_id": (), "attachment_run": ("delivery.check_attachments",), "case_run": ("semantics.approval_case", "governance.explain_findings"),
    "catalog_run": ("knowledge.register_catalog",), "changes_run": ("annual.compare",),
    "document_run": ("semantics.read_documents",), "history_run": ("annual.compare",),
    "prior_case": ("semantics.approval_case", "governance.explain_findings"),
    "raster_run": ("raster.clip",), "rules_run": ("knowledge.find_rules",),
    "spatial_run": ("control_check",), "summary_run": ("annual.summarize",), "vector_run": ("delivery.clip_vector",),
}

OVERRIDES = {
    ("presentation.export_table", "name"): "成果文件名，不包含扩展名或目录。",
    ("presentation.export_grouped_kml", "style"): "可选，使用地图图层的符号样式及分类颜色。",
    ("presentation.export_grouped_kml", "name"): "成果 ZIP 文件名称，不包含扩展名或目录。",
    ("xy.points", "x_field"): "源表的横坐标字段，地理坐标系对应经度。",
    ("xy.points", "y_field"): "源表的纵坐标字段，地理坐标系对应纬度。",
    ("xy.points", "source_crs"): "坐标值本身使用的二维地理或投影坐标系，例如 EPSG:4490；单位与该坐标系一致。",
    ("xy.points", "sheet"): "XLSX 工作表名称，多工作表时必填；CSV 省略。",
    ("geometry.create_rectangle", "left"): "左边界横坐标，单位与目标坐标系一致。",
    ("geometry.create_rectangle", "right"): "右边界横坐标，单位与目标坐标系一致。",
    ("table.add_field", "field"): "新字段的名称、数据类型和约束。",
    ("table.calculate_field", "field"): "接收计算结果的目标字段。",
    ("table.calculate_geometry", "field"): "写入几何量的目标字段。",
    ("table.summarize", "group_by"): "用于分类的真实字段名列表。省略时仅统计合计。",
    ("table.summarize", "measure"): "count 统计数量，sum 汇总数值字段，area 汇总各要素面积（平方米）。",
    ("table.summarize", "field"): "sum 必须指定数值字段；area 可传已核实单位为平方米的面积字段，否则按几何计算。",
    ("table.summarize", "offset"): "分组结果的起始序号，默认 0。后续分页使用返回的 next_offset。",
    ("table.lookup", "field"): "需要返回实际值的字段，匹配条件在 where 中指定。",
    ("cad.convert", "layer"): "DXF 图纸内的原始图层名称。",
    ("vector.dissolve", "fields"): "用于融合分组的字段。空列表表示整体融合。",
    ("edit.transfer", "mode"): "复制或剪切源选区。",
    ("data.select_by_attribute", "mode"): "创建、添加、移除或缩小已有选区。",
    ("data.select_by_location", "mode"): "创建、添加、移除或缩小已有选区。",
}


def parameter_help(operation, field):
    if field in RUN_INPUTS:
        sources = "、".join(RUN_INPUTS[field]) or "任意已登记操作"
        return {"description": "已完成的作业编号；来源：" + sources, "accepts_reference": "$run.<step_id>"}
    info = {"description": OVERRIDES.get((operation, field)) or PARAMETERS[field]}
    if field in ("selection", "previous"):
        info["accepts_reference"] = "$selection.<step_id>"
        info["map_selection_reference"] = "使用当前地图图层的 selection_reference，图层输入仍引用该原图层。"
    return info
