# GeoPilot

GeoPilot 是面向 ArcMap（ArcGIS Desktop）的本地 AI GIS 执行系统。自然语言只用于产生受限的结构化合同；GIS 操作只能经 CapabilityRegistry、租约绑定的 ArcMap Runtime、隔离暂存和独立验收执行。

## 唯一生产链

```text
Web Console -> HTTP Adapter -> GeoPilotKernel / JournalStore
-> frozen context lease -> TaskContract -> ProofGraph-verified plan
-> explicit authorization -> ArcMap Python 2 staged execution
-> independent ArcPy probe -> AcceptancePublisher -> atomic publication
```

每个 run 独立于 session、调用者、目标 ArcMap 和上下文摘要。JournalStore 追加记录请求、合同、证明、授权、回执、探针和发布事实；回执只能证明调度，不能证明 GIS 语义。任何未证明或违反的必须义务均不得发布。

## 合同和验证

- `shared_runtime.semantic_abi` 是 Python 3 / ArcGIS Python 2 共用的 GIS 语义 ABI：`Quantity`、`FieldSpec`、`SpatialPredicate` 与 `LineageFact`。
- 操作目录包含 9 个 pack 的可执行能力。每个能力必须有输入、参数、前置/后置条件、语义效果、输出、血缘、副作用、授权和验收合同，否则 CapabilityRegistry 拒绝注册。
- WorkflowVerifier 为请求/合同/实体/字段/选择/空间关系/单位/顺序/输出/血缘/副作用/授权生成唯一三态 ProofGraph：`Proven`、`Unresolved` 或 `Violated`。
- G3 只审计 G2 的同一 Intent、Context、CapabilitySnapshot 和 baseline；它只能引用已有 `proof_id`，不能生成替代规划。任何修订必须保持输入、输出、已证明事实和授权范围，并通过单调验证。
- 澄清是唯一的闭环：`POST /api/v1/runs/{id}/clarifications` 提交绑定 run、session、caller、request/context digest 与 clarification id 的答案。普通 `/resume` 不会重放澄清。

## 模型连接

生产模型调用为 `ModelRuntime -> ProviderRegistry -> ProviderAdapter`。MiniMax、DeepSeek、Qwen、智谱、Ollama、本地部署和 OpenAI-compatible 连接都必须显式配置；不存在自动换模型或隐式 fallback。第三章正式实验由 ExperimentSupervisor 单独锁定 `provider=minimax`、`model=MiniMax-M3`，该限制不改变日常生产连接。

凭据只保存为 CurrentUser DPAPI 引用，任务与日志绝不保存明文 key。

## 运行

- Windows 10/11 x64、ArcGIS Desktop / ArcMap 10.x（Python 2.7）
- Python 3.11 网关
- 已加载的 ArcMap Bridge 和显式配置的模型连接

```powershell
python -m gateway_py3
```

网关只监听 `127.0.0.1:8765`。不要直接调用 Bridge 回调或从 shell 执行 ArcPy；它们会绕过租约、授权、暂存、验收和原子发布。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py"

$env:PYTHONPATH = (Get-Location).Path
& 'C:\Python27\ArcGIS10.2\python.exe' -m unittest discover -s tests\python2_runtime -p 'test_*_py2.py'

Get-ChildItem gateway_py3\web -Filter *.js | ForEach-Object { node --check $_.FullName }
git diff --check
```

测试不调用真实模型或运行实验。正式实验只能通过 `python -m experiments.supervisor` 的受监督 runtime gate 进入。

当前架构与 Bridge 协议分别见 [目标架构](docs/GEOPILOT_TARGET_ARCHITECTURE.md) 和 [Bridge 租约协议](docs/GEOPILOT_BRIDGE_LEASE_PROTOCOL.md)。

## 目录

```text
gateway_py3/kernel/       Kernel、不可变合同与 JournalStore
gateway_py3/intelligence/ TaskCompiler、规划和受限审计
gateway_py3/runtime/      Bridge、上下文、验收与发布
arcmap_runtime_py2/       ArcPy 执行、探针和 durable outbox
operation_catalog/        严格验证的 9-pack 能力目录
experiments/supervisor/   G2/G3 受监督实验入口
shared_runtime/           Python 2/3 共用合同
tests/                    Python 3 与 ArcMap Python 2 合同测试
```
