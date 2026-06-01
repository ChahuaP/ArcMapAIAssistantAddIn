# GeoPilot

GeoPilot is a local AI workbench for ArcMap. It lets users describe GIS tasks in natural language, reviews the generated workflow, and executes only registered ArcPy operations inside ArcMap.

The project is built for ArcGIS Desktop / ArcMap, not ArcGIS Pro.

## What It Does

- Opens local GIS files and folders from natural language requests.
- Reads the current ArcMap context: layers, fields, selections, coordinate system, MXD status, and default geodatabase.
- Plans workflows with a local Python 3 gateway and selectable LLM providers.
- Executes approved ArcPy operations from a fixed operation catalog.
- Supports common map, layer, selection, analysis, table, and export operations.
- Provides a local Web console for conversation, task review, API key configuration, capabilities, and workflow queue.
- Packages into a single Windows setup executable, so normal users do not need to install Python 3.

## Safety Model

The model never runs arbitrary Python and never calls ArcPy directly.

The execution path is:

1. ArcMap synchronizes the current GIS context to the local gateway.
2. The selected model plans a workflow by using whitelist tools exposed by the gateway.
3. The gateway validates the workflow against the operation catalog.
4. The user approves the task in the Web console.
5. ArcMap pulls the approved workflow and executes only registered ArcPy operations.

Direct data edits, such as field updates, feature deletion, and field deletion, require a second confirmation in ArcMap before execution.

## Requirements

For end users:

- Windows
- ArcGIS Desktop / ArcMap
- DeepSeek API key, MiniMax Token Plan API key, or DashScope API key
- A release package built from this repository

For developers:

- Python 3 for the local gateway
- ArcGIS Desktop Python 2.7 for the ArcMap Add-in runtime
- PowerShell 7
- PyInstaller for building the bundled gateway executable
- Inno Setup 6 for building `GeoPilotSetup.exe`

## Install From Release Package

Do not install from the source tree directly.

Download the release package from GitHub Releases and run:

```text
GeoPilotSetup-<version>.exe
```

The installer opens like a normal Windows setup program and automatically requests administrator permission when installing to `C:\Program Files\GeoPilot`.

After installation, open ArcMap and enable the toolbar if needed:

```text
Customize > Toolbars > ArcMap AI Assistant
```

## Basic Use

1. Click `启动网关` in the ArcMap toolbar.
2. Click `显示控制台` to open the local Web console.
3. Configure the model API key in the Web console.
4. Click `同步上下文` in ArcMap.
5. Type a GIS task in the Web console.
6. Review and approve the generated task.
7. Click `执行工作流` in ArcMap.

Example requests:

- `缩放到 nanjing 图层`
- `选择 nanjing 中 NAME 等于 鼓楼区 的要素`
- `给 roads 做 100 米缓冲区，输出到 D:\Data`
- `打开 D:\Data\shapefile 下所有 shp`
- `把 nanjing 当前选中的要素导出到 D:\Data，输出名 nanjing_selected`
- `把 nanjing 图层按 NAME 字段拆分导出为 shp，输出到 D:\Data`

## Build A Release

From the repository root:

```powershell
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_release.ps1 -BuildGateway -BuildInstaller
```

The final release directory contains only:

```text
release\GeoPilotSetup-<version>.exe
release\geopilot-arcmap\
```

## Development

Start the Python 3 gateway:

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
# or
$env:DASHSCOPE_API_KEY = "your-bailian-key"
python -m gateway_py3
```

Build the ArcMap Add-in:

```powershell
python .\ArcMapAIAssistantAddIn\makeaddin.py
```

The generated Add-in file is:

```text
ArcMapAIAssistantAddIn\ArcMapAIAssistantAddIn.esriaddin
```

Run tests:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

## Project Layout

```text
ArcMapAIAssistantAddIn/   ArcMap Python Add-in shell
arcmap_runtime_py2/       ArcMap-side Python 2 runtime and ArcPy executor
gateway_py3/              Local Python 3 gateway, Web console, agent planner
operation_catalog/        Registered GIS operation specs and schemas
packaging/                Installer, uninstaller, and PyInstaller build scripts
tests/                    Release smoke tests
```

## Add Operations

To add a new GIS operation:

1. Add an operation spec in `operation_catalog/packs/*.json`.
2. Add the ArcPy executor in `arcmap_runtime_py2/operations/`.
3. Add tests for the catalog spec, validation, and executor behavior.
4. Rebuild the Add-in or release package when needed.

Operation descriptions and execution code are intentionally separate. The model sees the catalog; ArcMap executes only registered executor functions.

## Current Limitations

- ArcMap basemap automation is not enabled. ArcMap can add WMS/WMTS manually through GIS Servers, but stable automated basemap creation needs a future ArcObjects or prepared `.lyr` implementation.
- The project targets ArcMap and ArcPy, not ArcGIS Pro.
- The Web console and gateway run locally on `127.0.0.1`.

## License

MIT License. See [LICENSE](LICENSE).
