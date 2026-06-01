---
name: geopilot-arcmap
description: "Use when Codex needs to inspect or operate ArcMap through the local GeoPilot gateway without calling GeoPilot's own model API: read ArcMap context, run agent diagnostics, read operation capabilities, draft GeoPilot workflow JSON, validate it locally, submit or execute it through ArcMap Bridge, or help with ArcMap GIS tasks such as opening layers, selecting features, analysis, export, geometry creation/editing, data management, layout export, and custom tool drafts."
---

# GeoPilot ArcMap

## Workflow

Use the local GeoPilot gateway and `ArcMapBridge.exe` as the ArcMap safety and execution bridge. Do not call GeoPilot `/plan`; the active agent does the planning.

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
5. Run `scripts/geopilot_cli.py arcmap-sync` to make the selected ArcMap read the current context.
6. Run `scripts/geopilot_cli.py capabilities` to inspect the operation summary and choose only registered operations.
   - For exact argument schemas, run `scripts/geopilot_cli.py capabilities --detail` before drafting the workflow.
7. Draft a workflow JSON locally. For non-trivial planning, read `references/planning-contract.md` and `references/workflow-format.md`.
8. Validate with `scripts/geopilot_cli.py validate --workflow workflow.json`.
9. If validation fails, fix the workflow and validate again.
10. If the user confirmed this run, execute with `scripts/geopilot_cli.py arcmap-execute-workflow --confirmed --command "<user request>" --workflow workflow.json`.
11. If the user enabled full auto, first run `scripts/geopilot_cli.py arcmap-permission --auto-execute`, then execute without asking again.

Use `propose`, `approve-latest`, and `arcmap-execute-approved` only when the user explicitly wants queue review before execution.

## Hard Rules

- Never execute ArcPy directly from Codex, Claude, WorkBuddy, shell, or this skill.
- Never invent operations. Use only operation ids returned by `capabilities`.
- Never call `/plan`; that would make GeoPilot call another model API.
- Never pass `output_path` in workflow arguments. GeoPilot creates it during ArcMap execution.
- Never write raw SQL where clauses. Attribute filters must use structured `where` objects.
- Every execute step must include `id`, `operation`, `arguments`, and `reason`.
- Writes-data operations must include `output_name`; preserve user naming intent when provided.
- Direct data edits remain protected by ArcMap-side confirmation.
- Full auto requires explicit user authorization. For direct source-data edits, require explicit `allow_edits`.
- Geometry creation follows user intent: create one new output layer when the user asks for a new shp/layer, append to `target_layer` only when the user explicitly asks to add features into an existing layer, and create separate layers only when the user asks for separate outputs.
- Do not plan “create many shapefiles then merge” for ordinary multi-feature creation. Use `features` arrays in `edit.create_*` when one output layer should contain multiple features.
- ArcGIS feature classes cannot mix point, line, and polygon geometry in one layer. If the requested features mix geometry types and the user asked for one shp, ask a clarification instead of forcing a merge.
- When multiple ArcMap instances are open, do not guess. Use `arcmap-list` and select the intended instance by `hwnd` before syncing or executing.
- Do not manually start `ArcMapBridge.exe`; run `arcmap-list` so the Gateway owns Bridge startup, target discovery, and shutdown.
- Geometry creation, data management, and layout export are normal catalog operations. Read capabilities first and use `edit.*`, `data.*`, and `layout.*` only when those operation ids are present.

## References

- Read `references/planning-contract.md` when choosing operations, resolving layer/field intent, handling output locations, or designing custom tools.
- Read `references/workflow-format.md` when writing workflow JSON, structured `where`, output arguments, or custom tool specs.
- Read `references/workflow-examples.md` when you need a known-good workflow pattern for common ArcMap requests.
