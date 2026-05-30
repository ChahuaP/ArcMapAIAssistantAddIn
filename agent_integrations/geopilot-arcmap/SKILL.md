---
name: geopilot-arcmap
description: "Use when Codex needs to inspect or operate ArcMap through the local GeoPilot gateway without calling GeoPilot's own model API: read ArcMap context, read operation capabilities, draft GeoPilot workflow JSON, validate it locally, submit it to the GeoPilot task queue, or help with ArcMap GIS tasks such as opening layers, selecting features, analysis, export, and custom tool drafts."
---

# GeoPilot ArcMap

## Workflow

Use the local GeoPilot gateway and `ArcMapBridge.exe` as the ArcMap safety and execution bridge. Do not call GeoPilot `/plan`; the active agent does the planning.

1. Run `scripts/geopilot_cli.py health`.
2. Run `scripts/geopilot_cli.py arcmap-list` before every ArcMap task. This is the required Bridge startup/discovery call.
   - `arcmap-list` calls the Gateway `/arcmap/bridges` endpoint; the Gateway starts `ArcMapBridge.exe` if it is not already running.
   - Do not wait for the user to start the Bridge and do not ask the user to click a toolbar button first.
   - If `arcmap-list` returns no bridges, report that ArcMap or the GeoPilot Add-in is not ready; do not continue to sync or execute.
3. If more than one ArcMap instance is live, ask the user which target to use, then run `scripts/geopilot_cli.py arcmap-select --hwnd <hwnd>`.
   - ArcMap launch alone does not keep the Bridge running.
   - The Bridge uses the ArcMap Running Object Table to find ArcMap windows, then invokes the existing GeoPilot Python Add-in commands inside ArcMap.
   - Bridge-triggered sync/execute is silent in ArcMap. Manual toolbar clicks still show ArcMap Add-in message boxes.
   - If a detected bridge port is occupied but does not respond, read `%LOCALAPPDATA%\ArcMapAIAssistant\logs\arcmap_bridge.log`.
4. Run `scripts/geopilot_cli.py arcmap-sync` to make the selected ArcMap synchronize the current context.
5. Run `scripts/geopilot_cli.py capabilities` and choose only registered operations.
6. Draft a workflow JSON locally. For non-trivial planning, read `references/planning-contract.md` and `references/workflow-format.md`.
7. Validate with `scripts/geopilot_cli.py validate --workflow workflow.json`.
8. If validation fails, fix the workflow and validate again.
9. If the user confirmed this run, execute with `scripts/geopilot_cli.py arcmap-execute-workflow --confirmed --command "<user request>" --workflow workflow.json`.
10. If the user enabled full auto, first run `scripts/geopilot_cli.py arcmap-permission --auto-execute`, then execute without asking again.

Use `propose`, `approve-latest`, and `arcmap-execute-approved` only when you need to split queueing and execution for debugging.

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
- When multiple ArcMap instances are open, do not guess. Use `arcmap-list` and select the intended instance by `hwnd` before syncing or executing.
- Do not manually start `ArcMapBridge.exe`; run `arcmap-list` so the Gateway owns Bridge startup, target discovery, and shutdown.

## References

- Read `references/planning-contract.md` when choosing operations, resolving layer/field intent, handling output locations, or designing custom tools.
- Read `references/workflow-format.md` when writing workflow JSON, structured `where`, output arguments, or custom tool specs.
