# ArcMap Harness

运行在 ArcMap 上的政务 GIS 智能执行系统：dsh（DeepSeek Harness）自主 Agent 作为大脑，边界服务器（`server/`）作为模型与 ArcMap 之间的**唯一通道**。自然语言驱动地图操作；模型不直接编写或运行 Python，一切 GIS 操作经边界工具、Bridge 租约、暂存执行与独立验收完成。

## 架构

```text
浏览器 / dsh web（ArcMap Harness 品牌化控制台，MiniMax-M3）
  ↓ MCP（stdio）
server/  边界服务器（56 个原生操作工具 + 4 个基础设施工具）
  ├─ 三态 pre-check：proven / unresolved（向用户澄清）/ violated（拒绝）
  ├─ op journal（op 级 GIS 事实日志）
  └─ Py2/Bridge 回调面（127.0.0.1:8765）
  ↓ Bridge 租约协议（127.0.0.1:8766）
C# Bridge（ArcMap 进程内）→ ArcMap Python 2 runtime（暂存执行 → 独立验收 probe → 原子发布）
```

**红线**：拒绝类强制全部在边界；dsh 侧插件只做询问（审批）、呈现与品牌。`code-runtime`（代码执行通道）在组合中显式禁用——模型无法绕过 ArcMap 门。

## 工具面（B 结构）

56 个操作从 `operation_catalog`（9 pack）**自动生成**为独立 MCP 工具（`layer__add_layer`、`analysis__buffer`、`context__list_layers`…），工具列表即能力清单；另有 `get_map_context`（实时重捕）、`get_boundary_status`（连接状态）、`verify_result`（独立验收）、`get_operation_history`（操作日志）四个基础设施工具。缺必填参数返回 unresolved 义务，Agent 向用户澄清后重调。

## 构建与安装

```powershell
pwsh -NoProfile -File packaging\build_harness.ps1     # 构建到 build/harness-staging（含语法门禁）
pwsh -NoProfile -File packaging\install_harness.ps1   # 安装替换（UAC）：Program Files\GeoPilot\harness + Add-in + dsh profile
```

安装后点 ArcMap 工具栏 **ArcMap Harness** 按钮 → 启动控制台（深浅色主题跟随 dsh 原生）。

## 测试

```powershell
python -m unittest discover -s tests/server -p "test_*.py"
```

## 目录

```text
server/                 边界服务器（自包含：目录/生成/审查/日志/回调面）
arcmap_runtime_py2/     ArcMap Py2 执行、验收 probe、durable outbox
ArcMapBridgeExternal/   C# Bridge（租约协议）
ArcMapAIAssistantAddIn/ ArcMap Add-in（ArcMap Harness 按钮）
operation_catalog/      56 能力目录（唯一能力事实源）
shared_runtime/         Py2/Py3 共享语义合同
dsh/                    品牌插件、状态面板、profile 组合
packaging/              构建与安装脚本
docs/adr/               架构决策记录
```

## 关键约束

- `VERSION` 钉在 **2.0.0**：已安装的 Py2 Add-in 以此校验回调服务器身份，升级需同步两端。
- MiniMax 走官方 Anthropic 兼容端点（`api.minimaxi.com/anthropic`）；OpenAI 兼容端点缺 SSE `[DONE]` 哨兵，禁用。
- dsh 版本钉 `0.1.2-alpha.3`；升级需跑场景回归。

架构决策与证据见 [ADR 0002](docs/adr/0002-arcmap-harness-boundary.md)。
