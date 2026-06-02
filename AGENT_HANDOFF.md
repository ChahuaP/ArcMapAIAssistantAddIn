# GeoPilot Agent Handoff

更新时间：2026-06-02

## 当前状态

- 仓库：`D:\Development\Python\Arcpy`
- 当前版本：`0.21.2`
- 本机安装目录：`C:\Users\user\AppData\Local\ArcMapAIAssistant\app`
- 本机网关：`http://127.0.0.1:8765`
- 当前 release：`release\GeoPilotSetup-0.21.2.exe` 和 `release\geopilot-arcmap`
- 当前能力数：`/health` 返回 `operation_count=62`
- 当前本机 `/config` 已无 `config_error`

## 项目边界

GeoPilot 是 ArcMap 本地 AI 工作台，不是 ArcGIS Pro 项目。

核心链路：

1. `gateway_py3` 是 Python 3 本地网关，负责 Web、模型配置、planner、agent engine、事件流、workflow store、外部 agent API。
2. `ArcMapAIAssistantAddIn` 是 ArcMap Python Add-in，目前只保留一个按钮：`启动控制台`。
3. `arcmap_runtime_py2` 是 ArcMap 内 Python 2.7 runtime，负责读取上下文和执行 catalog 中注册的 ArcPy operation。
4. `ArcMapBridgeExternal` 生成 `ArcMapBridge.exe`，通过 Running Object Table 找 ArcMap，并触发同一个 Add-in 内部命令做静默同步和执行。
5. `operation_catalog` 是唯一能力目录。模型不能绕过 catalog，不能直接执行任意 ArcPy。
6. `agent_integrations\geopilot-arcmap` 是给 Codex、Claude、WorkBuddy 等外部 agent 用的 skill。

安全边界：

- AI 不直接跑 ArcPy。
- workflow 必须过本地 validator。
- 外部 agent 不调用 GeoPilot 模型 API；外部 agent 自己规划，只调用 `/context`、`/api/capabilities`、`/agent/workflows/validate`、`/agent/workflows/propose` 等本地接口。
- 直接改数据的操作仍走权限与确认边界。
- 不做 fallback、旧链路兼容、临时补丁。

## 重要目录

- `gateway_py3\app.py`：网关入口和 `APP_VERSION`
- `gateway_py3\llm_providers.py`：模型供应商、模型选项、API Key 解析、配置保存
- `gateway_py3\web\app.js`：Web 主逻辑，含模型配置 UI
- `gateway_py3\routes\common.py`：HTTP 入参过滤，包括配置 payload
- `gateway_py3\agent_engine`：agent loop、tool runtime、trace、progress event
- `arcmap_runtime_py2\gateway_client.py`：ArcMap runtime 期望的网关版本
- `ArcMapBridgeExternal`：Bridge EXE 源码
- `ArcMapAIAssistantAddIn`：ArcMap Add-in 包
- `operation_catalog\packs`：能力目录
- `packaging`：打包、安装、卸载脚本
- `release`：最终交付目录，只应放安装器 exe 和 skill
- `CONTEXT.md`：长上下文日志

## 最新关键改动

0.21.2：

- 修复旧配置无法通过网页保存修复的问题。
- 旧配置无效时，`save_config()` 会宽松载入旧值，先应用新 patch，再校验最终配置。
- 本机已把旧 `deepseek-chat` / `deepseek-v4-flash` 修成 `deepseek-v4-flash-thinking`，把旧 `glm-5.1` 修成 `glm-5.1-thinking`。
- 未改动用户 API Key 明文。

0.21.1：

- 模型配置每个 Key 输入项新增 `清除` 按钮。
- 输入框空着表示“不修改”。
- 只有点击 `清除` 并保存，才会通过 `clear_secret_fields` 删除配置文件里的对应 Key。

0.21.0：

- 阿里百炼支持普通 API Key 和 Token Plan API Key。
- 阿里百炼 Key 优先级：
  `providers.qwen.token_plan_api_key` > `BAILIAN_TOKEN_PLAN_API_KEY` / `DASHSCOPE_TOKEN_PLAN_API_KEY` > `providers.qwen.api_key` > `DASHSCOPE_API_KEY` / `QWEN_API_KEY` / `BAILIAN_API_KEY`
- Web 只显示 Key 状态和来源，不显示 Key 明文。
- Qwen-ASR 语音识别复用阿里百炼 Key 解析逻辑。

## 开发机打包

必须使用 PowerShell 7，并先设置 UTF-8：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; .\packaging\build_release.ps1 -BuildGateway -BuildInstaller'
```

成功后检查：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; Get-ChildItem .\release | Select-Object Name,Length,LastWriteTime'
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; Get-Content -Encoding UTF8 .\build\release_staging\ArcMapAIAssistant\app\VERSION'
```

当前期望：

- `release\GeoPilotSetup-0.21.2.exe`
- `release\geopilot-arcmap`
- staging `VERSION=0.21.2`

注意：

- `release` 目录只保留 exe 安装包和 skill，不再交付文件夹安装形式。
- 每次代码影响 agent 调用方式时，必须同步更新 `agent_integrations\geopilot-arcmap` 和 release skill。
- 不要把开发机 staging 安装方式写给普通客户。

## 开发机本地安装

本机安装前先停掉可能占用目录的进程：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; Get-Process ArcMapAIAssistantGateway,ArcMapBridge -ErrorAction SilentlyContinue | Stop-Process -Force'
```

再安装 staging 包到本机用户目录：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; .\build\release_staging\ArcMapAIAssistant\packaging\install.ps1 -InstallDir "C:\Users\user\AppData\Local\ArcMapAIAssistant\app" -Quiet'
```

启动并验证网关：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; $exe="C:\Users\user\AppData\Local\ArcMapAIAssistant\app\gateway\ArcMapAIAssistantGateway.exe"; Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe) -WindowStyle Hidden; Invoke-RestMethod http://127.0.0.1:8765/health'
```

本机 Add-in 会安装到所有已有的：

- `C:\Users\user\Documents\ArcGIS\AddIns\Desktop10.1\{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}`
- `C:\Users\user\Documents\ArcGIS\AddIns\Desktop10.2\{7f42eea1-1f17-4cf4-9d4f-c0c8d28c0a23}`

重要坑：

- 不要在外层双引号 PowerShell 命令里写 `"$env:LOCALAPPDATA\..."`，之前因此参数串位，把安装文件误放到 `D:\` 根目录。
- 开发机本地安装命令建议直接写绝对路径，或用单引号包 `pwsh -Command`。

## 客户机安装

交付给客户：

- `GeoPilotSetup-0.21.2.exe`
- `geopilot-arcmap` skill 文件夹

客户安装：

1. 双击运行 `GeoPilotSetup-0.21.2.exe`。
2. 默认安装到 `C:\Program Files\GeoPilot`，安装器会请求管理员权限。
3. 打开 ArcMap。
4. 如工具栏没显示，在 ArcMap 中启用：
   `Customize > Toolbars > ArcMap AI Assistant`
5. 点击工具栏里的 `启动控制台`。
6. 在 Web 右上角 `模型配置` 填 API Key 和选择模型。

客户卸载：

- 通过 Windows 应用卸载或开始菜单 `卸载 GeoPilot`。
- 默认保留模型配置、API Key、自建工具、workflow 记录和日志。
- 卸载弹窗勾选 `同时删除用户配置和本地数据` 才会删除这些用户数据。

客户机常见验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

应返回当前版本和 `ok=true`。

## Skill 交付给外部 Agent

Skill 位置：

- 开发源：`agent_integrations\geopilot-arcmap`
- release：`release\geopilot-arcmap`

外部 agent 应读取：

- `SKILL.md`
- `references\planning-contract.md`
- `references\workflow-format.md`
- `references\workflow-examples.md`
- `scripts\geopilot_cli.py`

外部 agent 工作流：

1. 启动或连接本地网关。
2. 获取 ArcMap context。
3. 获取 capabilities。
4. 生成 workflow JSON。
5. 调 `/agent/workflows/validate`。
6. validate 失败则按错误修正。
7. validate 通过后 propose 到队列。
8. ArcMap Bridge / ArcMap runtime 执行。

外部 agent 禁区：

- 不直接执行 ArcPy。
- 不跳过 GeoPilot catalog。
- 不直接写运行时输出路径字段 `output_path`。
- 不调用 GeoPilot `/plan` 去二次调模型。

## 模型配置要点

配置文件：

- `%APPDATA%\ArcMapAIAssistant\config.json`
- 备选检查路径：`%LOCALAPPDATA%\ArcMapAIAssistant\config.json`

运行时配置状态接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/config
```

模型选项必须保存 option id，不是随便填 API model：

- DeepSeek 示例：`deepseek-v4-flash-thinking`
- 智谱示例：`glm-5.1-thinking`
- MiniMax 示例：`MiniMax-M3`
- 阿里百炼示例：`qwen3.6-flash-2026-04-16`

如果看到：

```text
模型 deepseek-chat 不属于供应商 DeepSeek。
```

原因是旧配置还保存了旧模型名。0.21.2 后可以在网页模型配置里重新选择合法模型并保存修复。

## 验证命令

常用验证：

```powershell
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; python -m unittest discover -s tests -v'
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; python -m compileall -q gateway_py3 tests arcmap_runtime_py2'
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; node --check .\gateway_py3\web\app.js'
pwsh.exe -NoLogo -NoProfile -Command 'chcp.com 65001 | Out-Null; git diff --check'
```

最近一次结果：

- 全量测试：83 个通过
- JS 语法检查：通过
- Python compileall：通过
- `git diff --check`：通过，仅有 CRLF 提示

## 当前未提交源码改动

截至 2026-06-02，本轮相关源码改动集中在：

- `arcmap_runtime_py2\gateway_client.py`
- `gateway_py3\app.py`
- `gateway_py3\llm_providers.py`
- `gateway_py3\open_web.py`
- `gateway_py3\web\app.js`
- `tests\gateway\test_llm_providers.py`
- `tests\gateway\test_workbench_state.py`

如果继续开发，先看 `git status --short`，不要 revert 用户或其他 agent 的改动。

