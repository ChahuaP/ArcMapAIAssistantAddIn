"""Planner-visible operation semantics, shared with workflow validation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Contract:
    purpose: str
    roles: tuple[str, ...]
    products: tuple[str, ...]


def op(purpose, roles, products):
    return Contract(purpose, tuple(roles.split()), tuple(products.split()))


CONTRACTS = {
    "xy.inspect": op("列出 CSV 或 XLSX 坐标表的工作表、字段名称和表头问题。", "坐标表", "fields"),
    "xy.points": op("从 CSV 或 XLSX 的横、纵坐标字段生成点图层。每条数据行生成一个点，保留其属性。", "坐标表", "vector"),
    "data.copy": op("完整复制一个已有矢量图层。", "源图层", "vector"),
    "data.merge": op("将多个图层的要素纵向合并到新图层。", "待合并图层", "vector"),
    "data.append": op("以第一层为目标，将其余图层的数据追加入目标副本。", "目标图层 追加来源", "vector"),
    "data.describe": op("读取图层的空间参考、边界、几何类型和要素数量。", "待描述图层", "statistics count"),
    "data.list_fields": op("读取属性字段名、数据类型和字段结构。", "待查询图层", "fields"),
    "data.selection_count": op("核对图层版本并统计给定选区内的要素数。", "选区所属图层", "statistics"),
    "data.select_by_attribute": op("按字段条件在源图层选择要素。返回 statistics.selected_count（全部匹配数）和选区。", "被选图层", "selection statistics"),
    "data.select_by_location": op("按与第二图层的空间关系选择第一图层中的要素，返回选区和全部匹配数。", "被选图层 空间参照图层", "selection statistics"),
    "data.clear_selection": op("为当前图层生成空选区，清除高亮。", "被选图层", "selection statistics"),
    "data.export_selection": op("从选区所属图层提取所选要素到新图层。", "选区所属图层", "vector"),
    "vector.area": op("对面要素按条件汇总面积，结果单位为平方米。", "面积统计图层", "statistics area"),
    "vector.buffer": op("按指定距离生成缓冲面。", "缓冲对象图层", "vector"),
    "vector.clip": op("用第二个图层的覆盖范围裁切第一个图层。", "被裁切图层 裁切范围", "vector"),
    "vector.intersect": op("生成所有输入图层重叠部分并承接属性。", "参与相交的图层", "vector"),
    "vector.dissolve": op("按字段融合几何，未指定分组字段时整体融合。", "待融合图层", "vector"),
    "vector.spatial_join": op("按空间关系将第二层的属性连接到第一层要素。", "目标图层 连接图层", "vector"),
    "vector.erase": op("从输入要素中扣除第二层覆盖的部分。", "被擦除图层 擦除范围", "vector"),
    "vector.identity": op("用标识层分割输入要素并附加该层属性。", "输入图层 标识图层", "vector"),
    "vector.union": op("叠加面图层，保留各输入的覆盖区域和属性。", "参与联合的面图层", "vector"),
    "vector.symmetric_difference": op("保留两个图层各自独有的区域。", "第一面图层 第二面图层", "vector"),
    "vector.update_overlay": op("用第二层要素替换与第一层重叠的区域。", "待更新图层 更新图层", "vector"),
    "table.add_coordinates": op("将点的横纵坐标写入属性字段，使用图层坐标单位。", "点图层", "vector"),
    "table.add_field": op("按名称和数据类型增加属性字段。", "目标图层", "vector"),
    "table.delete_field": op("删除指定属性字段。", "目标图层", "vector"),
    "table.summarize": op("按完整匹配范围统计数量、字段之和或面积，可按多个属性分类。返回每组和总计，包含范围、字段、单位和有效值数。", "待统计图层", "statistics"),
    "table.lookup": op("返回满足条件的完整记录总数（statistics.lookup.count）及字段值样本（statistics.lookup.rows，最多 25 条）。查询不改变选区。", "待查询图层", "lookup statistics"),
    "table.calculate_field": op("根据字段、表达式或常量计算并更新指定字段。", "目标图层", "vector"),
    "table.calculate_geometry": op("按几何类型和单位将面积、长度或坐标写入字段。", "目标图层", "vector"),
    "table.edit_cell": op("按要素编号修改单个属性值。", "目标图层", "vector"),
    "table.update_rows": op("对满足属性条件的要素批量赋值。", "目标图层", "vector"),
    "table.delete_rows": op("删除满足属性条件的要素。", "目标图层", "vector"),
    "crs.assign": op("定义或覆盖空间参考，保留原坐标数值。", "待定义图层", "vector"),
    "crs.assign_from": op("为尚未定义空间参考的第一图层沿用第二图层的坐标系，坐标数值保持原样。", "待定义图层 参照图层", "vector"),
    "crs.transform": op("使用已登记转换方案重新计算坐标。", "待转换图层", "vector"),
    "crs.reproject": op("将矢量要素转换到目标坐标系。", "待投影图层", "vector"),
    "edit.changes": op("将一组创建、修改或删除操作应用于指定图层的工作副本。", "编辑图层", "vector"),
    "edit.transfer": op("在两个图层之间复制或剪切选中要素。", "选区所属图层 目标图层", "vector transfer-source"),
    "edit.transfer_plan": op("从源选区准备要粘贴到目标层的几何和属性。", "选区所属图层 目标图层", "features"),
    "geometry.create_dataset": op("按几何类型建立空图层。可提供一个空间参考图层。", "空间参考图层", "vector"),
    "geometry.create_rectangle": op("根据边界坐标新建包含矩形的面图层。", "空间参考图层", "vector"),
    "geometry.multipart": op("将同一图层中选中的 2 至 500 个要素组合为一个多部件要素，沿用属性来源要素的编号和属性。", "目标图层", "vector"),
    "geometry.explode": op("仅拆分选区中的多部件，各部件继承属性，选中的单部件保持原样。", "待拆分图层", "vector"),
    "geometry.repair": op("检查并修复不合法几何，可按参数剔除空几何。", "待修复图层", "vector"),
    "geometry.check_topology": op("只读统计空几何、自交等无效几何及同层面内部相交的要素对，返回数量和最多 30 条无效几何示例，不修改地图。", "待检查图层", "statistics values"),
    "map_raster.preview": op("生成可显示的栅格预览图。", "影像", "raster_preview"),
    "raster.statistics": op("读取空间参考、波段、像元统计和无数据像元情况。", "待统计栅格", "statistics count"),
    "raster.clip": op("按面图层限定的范围裁切栅格。", "待裁切栅格 裁切面图层", "raster"),
    "raster.reproject": op("按指定空间参考和重采样方法转换栅格。", "待投影栅格", "raster"),
    "raster.assign": op("覆盖栅格的坐标系定义，保持像元位置数值。", "待定义栅格", "raster"),
    "raster.mosaic": op("将已对齐的栅格按重叠处理规则拼接。", "待拼接栅格", "raster"),
    "imagery.classify": op("按影像识别模型和适用范围进行地类分类和边界提取。", "多波段影像 分类模型", "raster boundaries classification"),
    "imagery.change": op("对比前、后两期分类影像并生成变化区域。", "前期分类影像 后期分类影像", "raster boundaries change table report"),
    "cad.inspect": op("读取 DXF 文件的版本、图层和几何类型。", "CAD图纸", "drawing statistics"),
    "cad.convert": op("将指定 CAD 图层按已知空间参考转换为矢量图层。", "CAD图纸", "vector drawing table report"),
    "annual.summarize": op("按年份和地类汇总各期面积。", "按年份排列的地类图层", "table statistics"),
    "annual.compare": op("叠加两个年份的地类图层计算变化矩阵。", "前期地类图层 后期地类图层", "vector table"),
    "annual.report": op("根据既有年度汇总和变化步骤编制报告。", "", "table report"),
    "delivery.check_attachments": op("核对交付清单要求的附件是否齐备。", "附件", "attachments"),
    "delivery.clip_vector": op("用指定范围裁切矢量，仅保留授权字段。", "待共享图层 共享范围", "vector fields"),
    "delivery.bundle": op("汇集已核验的矢量、栅格和附件作业，生成交付包。", "", "package manifest table report"),
    "governance.farmland_balance": op("关联调出地、补划地和质量图层，核算面积及质量平衡。", "调出图层 补划图层 质量图层", "vector balance map table report"),
    "governance.permit_history": op("核对项目用地的手续覆盖和年度变化证据。", "项目用地 许可范围", "permit_history map table report"),
    "presentation.export_table": op("导出图层指定字段的属性表。", "属性表所属图层", "table"),
    "flow.unique_values": op("读取字段的不重复值，支持搜索，超过 500 项需缩小搜索范围。", "待查询图层", "values statistics"),
    "flow.condition": op("校验指定图层的结构化属性条件，供其他步骤重复使用。", "条件所属图层", "condition"),
    "flow.calculate": op("对两个数值进行四则运算。", "", "number"),
    "presentation.map_layout": op("设置版面、图名、图框和图例，可请求桌面导出。", "", "layout"),
    "presentation.map_view": op("对当前工作区应用图层定位、显示、设色或属性表指令。", "", "map_actions"),
    "system.health": op("检查独立处理服务的运行状态。", "", "health"),
    "evidence.verify": op("检验指定已完成作业的记录和文件。", "", "verification"),
    "evidence.history": op("查询工作区内作业执行记录。", "", "history"),
    "semantics.read_documents": op("读取审批文档，建立带来源页或片段的材料索引。", "审批材料", "document_index"),
    "semantics.approval_case": op("按地块编码和地域关联审批材料、空间要素及办理结论。", "项目地块图层", "case map report"),
    "knowledge.register_catalog": op("登记规则版本、业务术语和模型适用范围。", "", "knowledge_catalog"),
    "knowledge.extract_facts": op("从已有材料读取作业中核实结构化事实。", "", "facts"),
    "knowledge.map_terms": op("将输入术语与已登记规则目录中的术语匹配。", "", "term_mapping"),
    "knowledge.match_objects": op("按编码和地域在已有案例中匹配对象。", "", "object_matches"),
    "knowledge.find_rules": op("按行政区域、事项和日期解析适用规则。", "", "rule_resolution"),
    "knowledge.model_scope": op("检查指定模型版本是否适用于任务和地域。", "", "model_scope"),
    "governance.explain_findings": op("结合已有核查结论和规则依据生成解释及报告。", "", "case map report"),
    "control_check": op("叠加项目范围与管控范围，核对面积和覆盖比例并出具报告。", "项目范围 管控范围", "report map statistics conditional_vector"),
    "vector_inventory": op("读取并清点矢量要素、几何类型和空间参考，导出核验的数据。", "项目图层", "vector statistics"),
}

for shape, label in (("points", "点"), ("lines", "线"), ("polygons", "面"),
                     ("regular_polygons", "正多边形"), ("stars", "星形")):
    CONTRACTS["geometry.create_" + shape] = op("根据坐标新建含" + label + "要素的图层。", "空间参考图层", "vector")
    CONTRACTS["geometry.append_" + shape] = op("向已有图层追加" + label + "要素。", "目标图层", "vector")


# Only these response fields can be connected to later steps. Evidence files and
# query observations remain identifiable without implying a connectable value.
REFERENCES = {
    "vector": ("step", "vector"), "conditional_vector": ("step", "vector"), "raster": ("step", "raster"),
    "selection": ("$selection", "selection"), "area": ("$value", "number"),
    "count": ("$value", "integer"), "number": ("$value", "number"),
    "values": ("$value", "array"), "condition": ("$value", "condition"),
}

MAP_EFFECTS = {
    "vector": "可交付矢量图层", "conditional_vector": "按需生成叠加结果图层", "raster": "可交付栅格图层",
    "selection": "在原图层上应用选区", "map_actions": "更新当前地图视图", "layout": "更新地图版面",
    "map": "提供成果地图", "boundaries": "提供边界图层", "raster_preview": "显示栅格预览",
    "transfer-source": "剪切时提供移除所选要素后的源图层",
}

FILES = {
    "boundaries": "vector_file", "table": "table_file", "report": "report_file", "map": "map_file",
    "raster_preview": "image_file", "package": "archive_file", "transfer-source": "vector_file",
    "layout": "layout_definition", "map_actions": "map_commands",
}

CONDITIONS = {"conditional_vector": "output_layer=true、后续步骤以此作为图层输入或通过 spatial_run 读取空间核查成果",
              "transfer-source": "mode=cut", "area": "有效面积为空时返回 null"}


EXCLUSIVE_PARAMETERS = {
    "data.select_by_attribute": ("where", "sql"),
    "crs.reproject": ("definition", "plan"),
    "raster.reproject": ("target_crs", "plan"),
}

RESPONSE_FIELDS = {
    "vector": "export", "conditional_vector": "export", "raster": "export",
    "count": "value", "area": "value", "values": "value", "condition": "value", "number": "value",
}

DETAILS = {
    "xy.inspect": "fields 列出每个工作表的 sheet 和 fields；CSV 的 sheet 为 null，表头无效时返回 issue。",
    "data.describe": "value 是完整图层要素数，statistics.coordinate_system 为实际空间参考。",
    "data.selection_count": "statistics.selected_count 是给定选区的完整要素数。",
    "table.summarize": "statistics.summary.groups 是各组，total 是完整匹配范围的合计。scope=all_input 表示整份输入，filtered 表示仅筛选范围。match_status=no_matches 表示条件未命中。分页不改变合计。measure=area 时每组和总计的 area 给出平方米、亩、公顷和平方千米实值。",
    "table.lookup": "statistics.lookup.count 是全部匹配数；rows 只预览前 25 条，值可能重复。",
    "vector.area": "value 是平方米；statistics.area 为四种标准面积单位的实值；statistics.area_summary 给出数量和统计口径（按条件或逐要素统计时）。",
    "raster.statistics": "value 是波段数，statistics 和 raster 包含波段、范围和分辨率。",
    "flow.unique_values": "value 是全部匹配的唯一值列表；超过 500 项时要求缩小范围。",
    "presentation.map_layout": "export=true 时由桌面端应用版面后导出，仅完成本步骤时产物为版面定义。",
}

REQUIREMENTS = {
    "xy.points": "输入 CSV 或 XLSX；先查看表头，横、纵字段须不同，每行须有有效坐标。坐标系取自源表。",
    "data.select_by_location": "within_a_distance 时填写 distance；组合选区时填写 previous，须属第一图层同一版本。",
    "data.select_by_attribute": "组合选区时填写 previous，须属原图层同一版本。",
    "data.export_selection": "selection 必须来自第一图层的同一版本。",
    "data.selection_count": "selection 必须来自第一图层的同一版本。",
    "vector.area": "几何面积需面图层和投影坐标系。已有平方米数值字段可作为 area_field。",
    "vector.buffer": "distance 必须是有限非负值；实际距离单位须能转换到图层坐标单位。",
    "table.summarize": "按属性计数与求和可使用任意矢量图层；无面积字段时，几何面积须使用投影面图层。统计仅用于回答，保留地图图层和选区。",
    "table.calculate_geometry": "几何量须与图层几何类型匹配；面积、长度、周长和坐标使用对应单位。",
    "imagery.change": "两期分类影像需具有相同的范围、格网和空间参考。",
    "raster.mosaic": "输入栅格须有一致的坐标系、像元尺寸、对齐格网、波段和类型。",
    "raster.reproject": "源栅格须有空间参考。若指定 resolution，两个分量均须为有限正数。",
    "crs.reproject": "源图层须有已定义的空间参考。",
}


def connection(operation, prefix):
    for product in CONTRACTS[operation].products:
        ref = REFERENCES.get(product)
        if ref and ref[0] == prefix:
            return ref[1]
    return None


def describe_contract(operation, spec):
    contract = CONTRACTS[operation]
    inputs = [{"role": role, "kind": spec.kinds[min(i, len(spec.kinds) - 1)],
               "minimum": 0 if spec.minimum <= i else (spec.minimum - i if i == len(contract.roles)-1 else 1),
               "maximum": spec.maximum - i if i == len(contract.roles)-1 else 1}
              for i, role in enumerate(contract.roles)]
    outputs = []
    for key in contract.products:
        ref = REFERENCES.get(key)
        output = {"name": RESPONSE_FIELDS.get(key, key), "type": ref[1] if ref else FILES.get(key, "record")}
        if ref:
            output["reference"] = ref[0] + ".<step_id>"
        if key in CONDITIONS:
            output["when"] = CONDITIONS[key]
        outputs.append(output)
    outputs.append({"name": "run_id", "type": "run", "reference": "$run.<step_id>"})
    requirements = [REQUIREMENTS[operation]] if operation in REQUIREMENTS else []
    if operation in EXCLUSIVE_PARAMETERS:
        requirements.append("以下参数须恰好填一项：" + "、".join(EXCLUSIVE_PARAMETERS[operation]))
    if operation.startswith("geometry.create_"):
        requirements.append("一个空间参考图层与 epsg 编号二选一，须为二维地理或投影坐标系。")
    detail = {"response": DETAILS[operation]} if operation in DETAILS else {}
    effects = [MAP_EFFECTS[key] for key in contract.products if key in MAP_EFFECTS]
    if operation in {"geometry.multipart", "geometry.explode"}:
        effects = ["交付 export 时，destination=current 更新当前图层，保留未选要素；new 只添加选区处理结果图层。"]
    return {"usage": contract.purpose, "preconditions": requirements, **detail, "inputs": inputs, "outputs": outputs,
            "map_effects": effects,
            "writes_input_file": False}
