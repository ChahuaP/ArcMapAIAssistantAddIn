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

**红线**：拒绝类强制全部在边界；dsh 侧插件只做询问（审批）、呈现与品牌。`code-runtime`（代码执行通道）在组合中显式禁用——模型无法绕过 ArcMap 门。

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

## 测试

```powershell
python -m unittest discover -s tests/server -p "test_*.py"
```

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
