# GeoPilot Planning Contract

GeoPilot owns structured planning, validation, and controlled ArcMap execution.

## Core planning rules

- Use local facts before drafting: current ArcMap context, capability catalog, layer fields, sampled values when available, and explicit user paths.
- Prefer composing existing atomic operations. Common chains are add layer, inspect fields, select by attribute or location, export selected features, split by field, export KML/KMZ, create simple geometry, copy/repair/manage data, update existing layout text, export layout, clear selection, and zoom.
- Do not create a custom tool when an existing operation or operation chain can express the task.
- Use custom tools only for reusable GIS algorithms or processing primitives that cannot be expressed by the catalog.
- If an enabled `custom.*` operation already matches the goal, use it directly. Do not create a duplicate draft.
- If a pending or rejected custom tool matches the goal, tell the user it must be reviewed/enabled or revised before execution.
- If a custom tool has a bug or bad parameter design, revise the same tool. Do not create a second tool with the same purpose.
- For direct geometry creation, use `edit.*` operations before creating a custom tool. A request such as “创建一个五角星 feature” should map to `edit.create_star_polygon` when the user supplies or can confirm center/radius/coordinate system.
- Interpret “创建面图层，WGS84” as an empty polygon layer request and use `edit.create_empty_feature_layer` with `geometry_type=polygon` and `wkid=4326`; interpret “创建一个正方形/矩形/五角星/点/线” as a concrete feature creation request.
- For rectangles or squares described by upper-left and lower-right corners, use `edit.create_rectangle_polygon` with numeric `left/top/right/bottom`; do not hand-build coordinate arrays unless that operation is unavailable.
- Interpret “加载 shp/kml/tif” as `layer.add_layer`, and “复制某图层” as `data.copy_features`.
- Geometry creation must follow user intent:
  - If the user asks for one new shp/layer containing many features, use the `features` array on the matching `edit.create_*` operation and write one output dataset.
  - If the user explicitly asks to add features into an existing layer, use the matching `edit.append_*` operation with `target_layer`; this is `edits_data` and requires edit authorization.
  - If the user asks for separate outputs, create separate outputs.
  - Do not create many temporary shapefiles and merge them just to build ordinary multi-feature output.
  - Do not put mixed geometry types into one shapefile or feature class. ArcGIS feature classes are point, polyline, or polygon, not mixed geometry.
- For layout work, use `layout.list_elements` before `layout.set_text` unless the text element name is already known. GeoPilot can update existing layout elements and export the layout; it does not invent a new legend/scale bar/north arrow unless a registered operation says so.
- For data management operations marked `edits_data`, require the user's explicit `allow_edits` authorization because the original dataset is modified.

## Local file and output handling

- Do not invent or expand file paths. Use exact paths only when the user gave them.
- Do not recursively scan drive roots.
- Input file lookup belongs to the agent or a dedicated resolver; workflow steps only contain operation arguments.
- Output destinations must be existing folders or geodatabases when explicitly supplied.
- If the user did not provide an output location, let GeoPilot use the MXD/default geoprocessing output location.
- Use `output_folder` only when the operation schema declares it. Use `output_workspace` only when the schema declares it.
- `output_name` is only the base name: no folder, no extension, no dot, and no Windows-illegal characters.

## Geometry unit rules

- Geometry creation parameters that use `radius`, `distance`, or other coordinate offsets must include the matching `*_unit` parameter.
- Use `degrees` only when creating raw geometry in a geographic coordinate system.
- Use `meters` only when the target spatial reference is projected and ArcMap can convert meters to map units.
- Use `map_units` when the user is deliberately working in the current data frame's coordinate units.

## Layer, field, and attribute intent

- Use current ArcMap layers from context. Prefer `layer_ref` such as `layer:0` when names are ambiguous.
- Run-scoped outputs remain detached until authoritative execution is recorded. `layer.remove_layer` and `layer.move_layer` only accept live map layers, and `layer.clear_layers` must precede every run-scoped output.
- Treat UI mentions like `@图层名` and `#字段名` as markers; remove `@` and `#` in workflow arguments.
- Inspect fields and sampled values before translating vague natural-language attribute intent.
- Do not split Chinese natural language into conditions by simple keyword rules.
- Text contains is `{"field":"NAME","op":"like","value":"%南京%"}`.
- Boolean conditions must use `{"op":"and","conditions":[...]}` or `{"op":"or","conditions":[...]}`.

## Custom tool contract

- Custom executor code runs inside ArcMap Python 2.7.
- The executor must be `def execute(context, arguments, step_outputs):`.
- Do not use Python 3-only syntax or APIs: f-strings, type annotations, pathlib, dataclasses, async, `raise ... from ...`, `FileNotFoundError`, `os.scandir`, or `os.makedirs(..., exist_ok=True)`.
- Do not use ArcGIS Pro APIs: no `arcpy.mp`, no `ArcGISProject`.
- Do not call `arcpy.mapping.MapDocument`, `arcpy.mapping.ListLayers`, or inspect `CURRENT`.
- Do not call `getOutput`; GeoPilot passes ArcMap Layer objects and `arguments["output_path"]`.
- For writes-data custom tools, declare `output_name` and an `output_policy`; do not declare or accept `output_path` in the operation schema.
- File outputs may only open `arguments["output_path"]`.
- Unexpected geometry or ArcPy failures must raise. Do not hide them with broad `except`, `pass`, or empty success.
