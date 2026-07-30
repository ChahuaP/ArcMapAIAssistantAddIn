---
name: geopilot-arcmap
version: 1.0.1
description: "Use when Codex needs to run reproducible GeoPilot ArcMap experiments or inspect their results."
---

# GeoPilot ArcMap

## Workflow

Use the local GeoPilot gateway and `ArcMapBridge.exe` as the ArcMap safety and execution bridge. Use the four experiment modes through the single run interface.

1. Run `scripts/geopilot_cli.py health`.
2. On first run or when something looks wrong, run `scripts/geopilot_cli.py doctor`.
3. Run `scripts/geopilot_cli.py arcmap-list` before every ArcMap task. This is the required Bridge startup/discovery call.
   - `arcmap-list` calls the Gateway `/arcmap/bridges` endpoint; the Gateway starts `ArcMapBridge.exe` if it is not already running.
   - Do not wait for the user to start the Bridge and do not ask the user to click a toolbar button first.
   - If `arcmap-list` returns no bridges, report that ArcMap or the GeoPilot Add-in is not ready; do not continue to sync or execute.
4. If more than one ArcMap instance is live, ask the user which target to use, then run `scripts/geopilot_cli.py arcmap-select --hwnd <hwnd>`.
   - ArcMap launch alone does not keep the Bridge running.
   - The Bridge uses the ArcMap Running Object Table to find ArcMap windows, writes a silent command payload, then invokes the single GeoPilot Python Add-in command `openAssistantButton` inside ArcMap.
   - Bridge-triggered sync/execute is silent in ArcMap. A manual toolbar click on `启动控制台` opens the Web console and reads the current map context.
   - If a detected bridge port is occupied but does not respond, read `%LOCALAPPDATA%\ArcMapAIAssistant\logs\arcmap_bridge.log`.
5. Context capture is performed automatically for the exact selected ArcMap target when a run is submitted.
6. Create a run with `scripts/geopilot_cli.py run --mode context_single --command "<user request>"`.
7. Run automatically with `scripts/geopilot_cli.py run --mode multi_agent --command "<request>" --execute --confirmed`; direct edits additionally require `--allow-edits`.
8. Query the terminal result with `run-status <run_id>` or export reproducibility data with `run-report`.
   - `indeterminate` is terminal and means ArcMap's authoritative result did not arrive within the recovery window. Never infer success. The episode is protected as an audit record while a new run may be submitted independently. A later result is accepted only from the original execution owner and ArcMap target and is recorded as a recovery audit; ordinary cleanup is allowed only after that recovery.

The `run` command is the sole planning and controlled-execution entry point.

## Hard Rules

- Never execute ArcPy directly from Codex, Claude, WorkBuddy, shell, or this skill.
- Never invent operations. Use only operation ids returned by `capabilities`.
- Never pass `output_path` in workflow arguments. GeoPilot creates it during ArcMap execution.
- Never write raw SQL where clauses. Attribute filters must use structured `where` objects.
- Every execute step must include `id`, `operation`, `arguments`, and `reason`.
- Writes-data operations must include `output_name`; preserve user naming intent when provided.
- Direct data edits remain protected by ArcMap-side confirmation.
- Full auto requires explicit user authorization. For direct source-data edits, require explicit `allow_edits`.
- Geometry creation follows user intent: create one new output layer when the user asks for a new shp/layer, append to `target_layer` only when the user explicitly asks to add features into an existing layer, and create separate layers only when the user asks for separate outputs.
- Intent mapping is strict: “创建面图层，WGS84” means `edit.create_empty_feature_layer`; “创建一个正方形/矩形/五角星/点/线” means a concrete `edit.create_*` feature operation; “加载 shp/kml/tif” means `layer.add_layer`; “复制某图层” means `data.copy_features`.
- For upper-left/lower-right rectangle or square requests, use `edit.create_rectangle_polygon` with numeric `left/top/right/bottom`.
- Do not plan “create many shapefiles then merge” for ordinary multi-feature creation. Use `features` arrays in `edit.create_*` when one output layer should contain multiple features.
- ArcGIS feature classes cannot mix point, line, and polygon geometry in one layer. If the requested features mix geometry types and the user asked for one shp, ask a clarification instead of forcing a merge.
- When multiple ArcMap instances are open, do not guess. Use `arcmap-list` and select the intended instance by `hwnd` before executing.
- Do not manually start `ArcMapBridge.exe`; run `arcmap-list` so the Gateway owns Bridge startup, target discovery, and shutdown.
- Geometry creation, data management, and layout export are normal catalog operations. Read capabilities first and use `edit.*`, `data.*`, and `layout.*` only when those operation ids are present.

## References

- Read `references/workflow-format.md` when writing workflow JSON, structured `where`, output arguments, or custom tool specs.
- Read `references/workflow-examples.md` when you need a known-good workflow pattern for common ArcMap requests.
