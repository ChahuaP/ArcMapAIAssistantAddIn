---
name: geopilot-arcmap
description: "Use when Codex needs to inspect GeoPilot ArcMap capabilities and guide a user through the controlled web-console workflow."
---

# GeoPilot ArcMap

## Workflow

Use the local GeoPilot gateway and `ArcMapBridge.exe` as the ArcMap safety and execution bridge. This skill is read-only: task submission and control belong exclusively to the web console so target selection, session isolation, authorization, recovery, and SSE evidence remain on the single production path.

1. Run `scripts/geopilot_cli.py health`.
2. On first run or when something looks wrong, run `scripts/geopilot_cli.py diagnostics`.
3. Run `scripts/geopilot_cli.py arcmap-list` before every ArcMap task.
   - It lists Bridge instances that are already ready.
   - If it returns no bridges, report that ArcMap or the GeoPilot Add-in is not ready.
4. Run `scripts/geopilot_cli.py open-console`, then select the intended ArcMap target in the console. Never guess when multiple targets exist.
   - If a detected bridge port is occupied but does not respond, read `%LOCALAPPDATA%\ArcMapAIAssistant\logs\arcmap_bridge.log`.
5. Submit and control the task in the console. Do not synthesize HTTP write requests from this skill.
6. Follow the SSE-driven run state in the console. Never infer success from an indeterminate state.

The web console is the sole planning and controlled-execution entry point. The CLI is read-only.

## Boundaries

- Never execute ArcPy directly from Codex, Claude, WorkBuddy, shell, or this skill.
- Never bypass the console's target selector, authorization decision, lease fencing, staging, acceptance, publication, or recovery flow.
- Never invent operation availability. The read-only `capabilities` command reports the registered operation summaries.
- Never use this skill as an experiment runner; formal experiments use the supervised Kernel experiment path.
