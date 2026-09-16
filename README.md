# ArcMap Harness

运行在 ArcMap 上的政务 GIS 智能执行系统：dsh（DeepSeek Harness）自主 Agent 作为大脑，边界服务器（`server/`）作为模型与 ArcMap 之间的**唯一通道**。自然语言驱动地图操作；模型不直接编写或运行 Python，一切 GIS 操作经边界工具、Bridge 租约、暂存执行与独立验收完成。

## 架构

```text
浏览器 / dsh web（ArcMap Harness 品牌化控制台，MiniMax-M3）
  ↓ MCP（stdio）
server/  边界服务器（55 个原生操作工具 + 4 个基础设施工具）
  ├─ 三态 pre-check：proven / unresolved（向用户澄清）/ violated（拒绝）
  ├─ op journal（op 级 GIS 事实日志）
  └─ Py2/Bridge 回调面（127.0.0.1:8765）
  ↓ Bridge 租约协议（127.0.0.1:8766）
C# Bridge（ArcMap 进程内）→ ArcMap Python 2 runtime（暂存执行 → 独立验收 probe → 原子发布）
```

**红线**：GIS 数据与副作用约束由边界服务器强制执行；dsh 侧负责询问、呈现和模型循环停止。组合只向模型提供 59 个 GIS 工具和 1 个澄清工具，禁用代码、文件、网页、子 Agent、工作流及任务清单工具；启动后的工具目录也会校验这一边界。

## 工具面（B 结构）

55 个操作从 `operation_catalog`（9 pack）**自动生成**为独立 MCP 工具（`layer__add_layer`、`analysis__buffer`、`context__list_layers`…），工具列表即能力清单；另有 `get_map_context`（实时重捕）、`get_boundary_status`（连接状态）、`verify_result`（独立验收）、`get_operation_history`（操作日志）四个基础设施工具。缺必填参数返回 unresolved 义务，Agent 向用户澄清后重调。

## 构建与安装

一键安装包（推荐给用户，**零环境依赖**）：

```powershell
pwsh -NoProfile -File packaging\build_installer.ps1   # 构建 → release\ArcMapHarnessSetup-<version>.exe
```

安装包自带全部运行时，用户只需双击（UAC 同意即可），**无需安装 Python / Node / pnpm / dsh，也无需配置环境变量**：

- `runtime\node\node.exe`：内置便携 Node
- `runtime\dsh\...`：内置 dsh 0.1.2-alpha.3（含依赖）
- `runtime\python\...`：内置 embeddable Python 3.11 + fastmcp/pydantic
- `dsh-profile\...`：预装好的 dsh profile（hoisted，无符号链接，安装时直接拷贝，不再联网/跑 pnpm）

安装流程：**自动卸载任何旧版本**（1.x 便携版、2.0 GeoPilot/ArcMapAIAssistant、旧 Add-in、旧注册表卸载项、旧进程）→ 安装到 `C:\Program Files\ArcMap Harness\harness` → 部署 ArcMap Add-in 与 per-user dsh profile。用户数据（API Key、模型配置、自建工具、日志）默认保留。首次点击 Add-in 按钮会弹窗让用户填写 MiniMax API Key。

> 构建安装包需要联网（拉取 dsh 依赖、Python embeddable、pip 包）；装到用户机器时不再需要网络（除模型调用外）。

开发者分步：

```powershell
pwsh -NoProfile -File packaging\build_harness.ps1     # 构建（含运行时、C# Bridge、语法门禁）到 build/harness-staging
pwsh -NoProfile -File packaging\install_harness.ps1   # 安装（UAC）：Program Files\ArcMap Harness + Add-in + dsh profile
pwsh -NoProfile -File packaging\uninstall_harness.ps1 # 卸载
```

安装后点 ArcMap 工具栏 **ArcMap Harness** 按钮 → 启动控制台（深浅色主题跟随 dsh 原生）。

## 本地 Ollama 与控制台更新

本地模型使用 `qwen2.5:7b-arcmap`。它复用 `qwen2.5:7b` 的模型权重，通过 Ollama 原生 API 明确设置 `num_ctx=32768`、`num_predict=2048`；仅设置 OpenAI 兼容客户端的 contextWindow 不会改变服务端上下文。先在 Ollama 安装 `qwen2.5:7b`，再运行安装目录中的 `configure_ollama.ps1 -SelectAsDefault`。配置脚本不会下载模型，也不会切换到云模型。

只更新现有安装的控制台、保留 ArcMap 和当前地图运行：

```powershell
pwsh -NoProfile -File packaging\update_console.ps1 -SelectLocalModel
```

更新后重新点击 ArcMap Harness 按钮并新建对话，选择“Qwen2.5 7B”。旧会话保留原始模型选择和历史内容，不应用于修复验收。

`dsh/plugins/arcmap-agent` 提供唯一的中文系统提示、工具目录校验和停止条件：同一操作两次失败、连续三次同参数调用或单轮达到 32 步时，显式报错并停止。每轮本地调用前检查 Ollama 模型实际参数及工具能力，配置不正确时拒绝运行。

完整安装与控制台更新共用 `install_profile.ps1`，从同一份 `cordis.patch.yml` 生成安装配置。新插件从当前源码打包，不使用运行时缓存里的过时插件副本。

## 测试

模型只填写业务参数。`server/tool_contract.py` 从能力目录生成 MCP schema，并确定性生成运行时需要的常量和默认字段。距离示例为 `{"value":1,"unit":"kilometers"}`，维度和内部容差由程序填写；起始角度直接填写数字。字段定义最少提供 `name`、`type`。原始内部 Quantity 对象不是公开工具接口。

所有 55 个操作共用 JSON Schema 校验，声明真实必填字段、嵌套结构和枚举。无效输入不会被自动转换为其他业务值，条件值和属性赋值中的空字符串、`null`、数字字符串按原样保留。可选命名参数的 `null` 表示省略。图层重名时必须明确引用，整条校验与派发过程串行执行。新建几何必须提供 `wkid` 或明确的参考图层。

运行时统一将输出加入活动数据框并检查实际图层，失败清理本次输出和图层；回执重传不重复执行。Buffer 固定使用当前 ArcGIS Python 的独立进程，禁止输出覆盖，缺少解释器时明确报错。更新运行时源码后需要保存地图并重启 ArcMap。

真实本地小模型首调用检查：`python tests/agent/check_local_arguments.py`（需将项目目录加入 Python 模块路径）。使用生产 MCP schema 和 Ollama OpenAI 接口，只生成参数，不执行 GIS 写操作。报告位于 `build/local-arguments-report.json`。

真实 ArcGIS 执行回归：`C:\Python27\ArcGIS10.2\python.exe tests/python2_runtime/real_tool_contract_py2.py`，覆盖 Buffer 独立进程、实际字段长度、角度、地图加入和失败清理。仅使用本次创建的测试数据与内存中的地图模板。

```powershell
python -m unittest discover -s tests/server -p "test_*.py"
```

模型循环回归测试：`node --test tests/agent/guard.test.mjs`。

真实本地模型验收：先停止控制台（释放 8765），保持 ArcMap/Bridge 运行，执行 `pwsh -NoProfile -File tests/agent/run_acceptance.ps1`。该测试以网页相同的预设加载流程运行问候、能力咨询和只读图层查询，检查完整工具目录、中文结果与调用数量；结果写入 `build/agent-acceptance-report.json`。

`tests/python2_runtime/` 为 ArcMap 内 Py2 运行时测试，需在 ArcMap 自带的 Python 2.7 + `arcpy` 环境中运行：

```powershell
& "C:\Python27\ArcGIS10.x\python.exe" -m unittest discover -s tests/python2_runtime -p "test_*_py2.py"
```

## 目录

```text
server/                 边界服务器（自包含：目录/生成/审查/日志/回调面）
arcmap_runtime_py2/     ArcMap Py2 执行、验收 probe、durable outbox
ArcMapBridgeExternal/   C# Bridge（租约协议）
ArcMapAIAssistantAddIn/ ArcMap Add-in（ArcMap Harness 按钮）
operation_catalog/      55 能力目录（唯一能力事实源）
shared_runtime/         Py2/Py3 共享语义合同
dsh/                    品牌插件、状态面板、profile 组合
packaging/              构建与安装脚本
tests/                  server（Py3）与 python2_runtime（Py2）测试
```

## 关键约束

- `VERSION` 钉在 **2.0.0**：已安装的 Py2 Add-in 以此校验回调服务器身份，升级需同步两端。
- MiniMax 走官方 Anthropic 兼容端点（`api.minimaxi.com/anthropic`）；OpenAI 兼容端点缺 SSE `[DONE]` 哨兵，禁用。
- dsh 版本钉 `0.1.2-alpha.3` 并随安装包内置（`packaging\build_runtime.ps1`）；升级需重跑 `build_installer.ps1` 与场景回归。

架构约束与证据见上文「架构」「红线」两节及各模块 docstring。
