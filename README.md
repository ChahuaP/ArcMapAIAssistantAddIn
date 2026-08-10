# GeoPilot

GeoPilot 是面向 ArcMap（ArcGIS Desktop）的本地 AI GIS 系统。用户在 Web 控制台用自然语言描述任务；系统捕获所选 ArcMap 实例的真实上下文，生成并验证结构化计划，经用户明确授权后，在隔离暂存区执行 ArcPy 操作，独立验收成果并原子发布。

版本号只由仓库根目录的 `VERSION` 定义。源码、安装脚本、网关、Bridge 和 ArcMap Runtime 均从该文件读取或在发布阶段注入版本，不在其他文档或模块中复制版本常量。

## 唯一生产链

```text
Web Console
  -> HTTP Adapter
  -> GeoPilot Kernel / JournalStore
  -> Context lease + frozen ContextSnapshot
  -> TaskCompiler
  -> LangGraph planning workflow
  -> deterministic plan verification
  -> explicit authorization decision
  -> ArcMap Bridge
  -> Python 2 ArcMap Runtime / durable outbox
  -> independent acceptance probe
  -> transactional publication
  -> journal-backed SSE projection
```

系统只有这一条任务链：

- Web 控制台每次新任务创建独立会话，并要求用户选择完整、稳定的 ArcMap 目标。
- Kernel 是提交、查看、授权决定和恢复的唯一协调入口。
- 上下文、能力、意图、计划、授权、租约、回执、成果和发布事实写入追加式 JournalStore。
- TaskCompiler 和 WorkflowEngine 只调用中立 `ModelRuntime`；角色模型由显式 `AgentModelPlan` 绑定，不存在自动回退或自动换模型。
- 当前安装的第一个生产 Adapter 是 MiniMax，默认角色计划使用 `MiniMax-M3`；这不是 Kernel 或工作流的架构限制。
- 模型只负责结构化理解、计划生成和计划审计，不直接执行 ArcPy，也不决定授权或发布。
- 写操作只进入 run-scoped staging；最终目的地保留在封存计划和授权范围中。
- ArcMap Runtime 先把权威回执写入 durable outbox，再回调 Gateway。
- Gateway 独立读取并核验暂存成果；FileGDB 以完整逻辑清单和物理清单确认身份。
- 发布以 run 为事务边界；不能证明执行或发布状态时进入明确的 indeterminate 状态，不猜测成功。

## 运行要求

- Windows 10/11 x64
- ArcGIS Desktop / ArcMap 10.x 及其 Python 2.7
- Python 3.11（源码运行网关）
- 至少一个已安装 Provider Adapter 及其凭据（当前为 MiniMax Token Plan API Key）
- Chrome 或 Edge

发布构建还需要 Visual Studio、vcpkg、PyInstaller 和 Inno Setup 6。构建动作不会由普通测试命令隐式触发。

## 模型连接与角色计划

生产调用链是 `ModelRuntime -> ProviderRegistry -> ProviderAdapter`。`compiler`、`planner`、`auditor`、`repairer` 分别绑定 `ModelBinding`；每个绑定都包含连接、模型、角色、采样参数和 `TokenPlan`。Kernel、Agent 和 LangGraph 不导入任何供应商客户端。

当前仅安装 MiniMax Adapter 和官方连接，因此 Web 控制台只编辑该连接的 Key。新增 DeepSeek API、Ollama 或 OpenAI-compatible 连接时，只增加 Adapter、`ProviderConnection` 和 `AgentModelPlan`，不改 Kernel 或工作流。

Key 只能通过 Web 控制台右上角“模型配置”写入 CurrentUser DPAPI 凭据库：

```text
%APPDATA%\ArcMapAIAssistant\credentials.json
```

任务、模型计划和调用账本只保存 `credential_ref`，不保存明文 Key。配置接口只接受一次性 `api_key` 或明确清除请求；未知字段直接拒绝。

第三章实验有独立合同，强制 `provider=minimax` 且 `model=MiniMax-M3`。这个锁只属于 ExperimentSupervisor，不参与普通生产任务的架构决策。

JournalStore 对用户 Prompt、上下文、计划、授权、回执、模型响应/缓存和审计 payload 统一做 CurrentUser DPAPI 静态加密。每个 run 的审计事件还保存前序哈希和当前链哈希，启动和读取时校验篡改；旧数据库格式直接拒绝。

## 使用

1. 打开 ArcMap，并确保 GeoPilot Add-in 与 Bridge 已加载。
2. 点击 ArcMap 工具栏中的“启动控制台”。
3. 在控制台选择目标 ArcMap 实例。
4. 输入自然语言 GIS 任务。
5. 若计划包含副作用，核对风险级别、输入身份和最终输出路径后明确批准或拒绝。
6. 通过 SSE 查看当前事实状态；对 indeterminate 运行使用控制台提供的人工核对与恢复入口。

不要直接调用 Bridge 回调路由，不要从 shell 执行 ArcPy，不要绕过授权、暂存、验收或发布阶段。

## 源码运行

安装仓库锁定的依赖后，在仓库根目录运行：

```powershell
python -m gateway_py3
```

网关只监听 `127.0.0.1:8765`。浏览器访问 `http://127.0.0.1:8765/`。

## 测试

Python 3：

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

ArcMap Python 2：

```powershell
$env:PYTHONPATH = (Get-Location).Path
& 'C:\Python27\ArcGIS10.2\python.exe' -m unittest discover -s tests\python2_runtime -p 'test_*_py2.py'
& 'C:\Python27\ArcGIS10.2\python.exe' tests\python2_runtime\real_arcpy_probe_py2.py
```

前端语法与变更洁净度：

```powershell
Get-ChildItem gateway_py3\web -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

测试不会调用真实模型，也不会启动论文实验。真实模型门禁和第三章实验由 `gateway_py3/experiments/supervisor.py` 的受监督 planning-gate 流程单独执行。

## 发布

发布脚本从根 `VERSION`、源码树和 `agent_integrations/geopilot-arcmap` 生成交付物：

```powershell
.\packaging\build_release.ps1 -BuildGateway -BuildInstaller
```

生成目录 `release/` 不是源码。发布脚本会重新创建它，并输出：

```text
release/
  GeoPilotSetup-<VERSION>.exe
  geopilot-arcmap/
```

deployment hash 只覆盖实际打包内容，排除 `__pycache__` 与 `*.pyc`，并绑定 Gateway、Bridge 和 ArcMap Runtime 的部署身份。

## 目录

```text
ArcMapAIAssistantAddIn/   ArcMap Python Add-in 外壳
ArcMapBridgeExternal/     C# ArcMap Bridge
arcmap_runtime_py2/       ArcMap Python 2 执行、探针与 durable outbox
gateway_py3/api/          HTTP 输入适配
gateway_py3/kernel/       合同、协调器与 JournalStore
gateway_py3/intelligence/ TaskCompiler 与 LangGraph 规划
gateway_py3/model_runtime/ Provider 合同、注册表、凭据库、角色计划和调用账本
gateway_py3/runtime/      Bridge、上下文、策略、验收与发布适配
gateway_py3/experiments/  受监督 G2/G3 planning-gate 配对
gateway_py3/web/          Web 控制台与 SSE 客户端
shared_runtime/           Python 2/3 共用的纯合同和平台路径
operation_catalog/        严格验证的内置操作目录
agent_integrations/       只读 Agent 集成源码
packaging/                安装与发布脚本
tests/                    Python 3、Python 2 与真实 FileGDB 合同测试
```

目标架构和 Bridge lease 协议分别见 `docs/GEOPILOT_TARGET_ARCHITECTURE.md` 与 `docs/GEOPILOT_BRIDGE_LEASE_PROTOCOL.md`。
