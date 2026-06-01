# GeoPilot ArcMap Agent Skill

This package contains a portable agent skill for operating ArcMap through the local GeoPilot gateway without calling GeoPilot's own model API.

## What agents should install

Copy this folder into the agent's skill directory:

```text
agent_integrations/geopilot-arcmap
```

The copied skill folder must keep this structure:

```text
geopilot-arcmap/
  SKILL.md
  agents/openai.yaml
  references/planning-contract.md
  references/workflow-format.md
  scripts/geopilot_cli.py
```

## How to use after installing

1. Open ArcMap with the GeoPilot Python Add-in enabled.
3. Ask the agent to use `geopilot-arcmap`.
4. The agent should call `scripts/geopilot_cli.py` instead of GeoPilot `/plan`.
5. The agent must call `arcmap-list` before any ArcMap sync or execution. This call starts `ArcMapBridge.exe` through the Gateway and discovers ArcMap targets.
6. If multiple ArcMap instances are open, select one with `arcmap-select --hwnd <hwnd>`.
7. The agent should call `arcmap-sync` and `arcmap-execute-workflow` directly for normal ArcMap operation.

## Important boundary

- The agent plans workflow JSON.
- GeoPilot validates and queues the workflow.
- `ArcMapBridge.exe` accepts local HTTP requests, finds ArcMap through the Running Object Table, writes a silent command payload, and dispatches sync/execute through the single ArcMap Python Add-in command `openAssistantButton`. Bridge-triggered commands are silent; the manual toolbar button only starts the Web console.
- The agent must not execute ArcPy directly.
- The agent must not call GeoPilot `/plan`, because that would call another model API.
