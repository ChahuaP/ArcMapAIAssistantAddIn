# GeoPilot 架构重构 Agent 提示词

以下内容可直接复制给负责修改代码的 Agent。建议使用 `gpt-5.6-terra`、reasoning effort `medium`。

---

你接手 `D:\Development\Python\Arcpy` 的 GeoPilot 一次性架构重构。你的任务不是继续打补丁，也不是为了 G3/G2 实验数字做案例定制，而是把 GeoPilot 重构为可供真实政务用户使用的业务化 AI GIS。

开始前必须完整阅读：

1. 仓库及上级目录中的全部 `AGENTS.md`。
2. `D:\Development\Python\Arcpy\docs\GEOPILOT_TARGET_ARCHITECTURE.md`。
3. 与当前修改直接相关的源代码和测试；不能只根据文件名或旧报告推测行为。

`GEOPILOT_TARGET_ARCHITECTURE.md` 是唯一目标架构。发现文档与当前代码冲突时，以目标架构为重构方向；发现目标架构内部存在无法实现或会破坏正确性的矛盾时，先用代码证据说明具体分叉，不得自行引入临时兼容方案。

## 最终目标

实现以下生产链路：

```text
TaskSession
→ GeoPilotKernel
→ TaskCompiler
→ WorkflowEngine
→ PolicyGate
→ ArcMapRuntimeLease
→ AcceptancePublisher
→ authoritative RunOutcome
```

模型只理解任务、提出计划和审计计划。上下文绑定、能力选择、权限、计划验证、ArcMap 目标绑定、执行、验收和发布由确定性代码负责。

GeoPilotKernel 是 UI、HTTP、外部 Agent 和未来 ExperimentSupervisor 唯一可调用的深模块，只暴露：

```python
submit(request_envelope) -> RunView
inspect(run_id) -> RunView
decide(run_id, approval_decision) -> RunView
resume(run_id) -> RunView
```

## 强制规则

1. 不保留向后兼容。旧字段、旧数据库、旧路由、旧导入路径和旧执行链路迁移完成后直接删除；禁止 alias、compat、migration、fallback 和双轨运行。
2. 不创建分支或 worktree，不 commit，不 push，不 revert 用户改动。开始和每个阶段前检查 `git status --short`，保留所有不属于本任务的改动。
3. 使用 PowerShell 7。读取中文前执行 `chcp 65001` 并使用 `Get-Content -Encoding UTF8`。
4. 文件编辑使用 `apply_patch`。不得用脚本覆盖用户文件。
5. 当前禁止任何真实模型调用和第三章实验。所有规划测试使用离线 Fake Model Adapter。
6. 普通生产调用必须由 `AgentModelPlan` 显式绑定 provider/model/role；不可用就明确失败，不重试、不自动切换。第三章实验单独锁定 `provider=minimax`、`model=MiniMax-M3`。
7. 删除旧 `llm_providers.py` 松散框架、隐式选模和自动切换；保留小而严格的 Provider Adapter 扩展边界。语音能力不能形成第二条规划模型链路。
8. 不放宽验证器、不修改评分器、不制作案例专用 Prompt、不伪造结果。
9. 不执行打包、安装或正式实验。常规 Python/JavaScript/离线测试属于允许的验证；除非用户明确要求，不执行发布构建。
10. 所有临时检查脚本在验证后删除。
11. 核心合同和模型输出验证使用 Pydantic v2 `BaseModel`（§13.1）；`frozen=True` + `extra='forbid'` + `validate_assignment=True`。手写 `__post_init__` 校验和手写 JSON Schema 在阶段 B 迁移后删除。
12. WorkflowEngine 规划状态机使用 LangGraph `StateGraph`（§13.2）；检查点用 `SqliteSaver`，`thread_id = run_id`。LangGraph retry 必须关闭或限于网络类错误，不得自动重试 `QuotaStopped` 和 `ModelCallUncertain`。
13. 上下文值摘要惰性捕获（§4.2）；只读操作不触发全量值采样；执行后复核只捕获声明输出图层。
14. 模型调用和规划阶段进度通过 SSE 流式推送（§14）；前端移除退避轮询和假计时器。
15. `pydantic` 和 `langgraph` 必须在 `requirements.txt` 或 `pyproject.toml` 锁定精确版本。阶段 B 首次引入时在 Windows + Python 3.11 实测 `pip install`、`import`、最小端到端跑通三项后才继续。

## 必须实现的硬保证

- 未验证计划不能执行。
- 未授权副作用不能执行。
- 未绑定准确 lease、epoch、HWND、上下文和 plan_hash 的请求不能执行。
- 旧 Bridge 回调不能提交新任务结果。
- 未验收成果不能发布。
- 执行是否发生不确定时不能自动重放。
- 相同且已持久化成功的纯模型请求可以零模型调用复用。
- 新任务的对话、摘要、工具结果和 Agent 状态完全为空，旧任务不会进入模型上下文。
- 缓存不等于会话记忆；跨会话只允许复用严格内容哈希相同、机构和安全范围一致的纯结果。
- 每个正式结果可以追溯到请求、上下文、能力、模型、计划、授权、执行回执和验收报告。

## 实施方式

按阶段实施，每阶段先形成一个可运行的最小端到端切片，再扩大覆盖。同一能力进入新链路且新接口测试通过后，立即删除对应旧链路；不要让新旧架构长期并存。

### 阶段 A：核心合同、Session 和 Journal

1. 定义严格的 RequestEnvelope、ContextSnapshot、IntentSpec、CapabilitySnapshot、VerifiedPlan、AuthorizationGrant、RuntimeLease、RuntimeOutcome、AcceptanceReport、PublicationReceipt 和统一 Outcome。
2. 创建新的 SQLite schema，至少覆盖 sessions、runs、run_events、快照、模型调用、计划、租约、授权、执行回执、成果、验收和发布回执。
3. `run_events` 是追加写事实源，`runs` 是同事务更新的状态投影。
4. 旧数据库格式直接拒绝，不写 migration。
5. 实现 GeoPilotKernel 四个公开方法，并用 Fake Model/Fake ArcMap 跑通一个只读最小链路。
6. 实现服务端“新任务”：新 `session_id` 绝不读取旧会话消息。历史只用于审计；只有显式导入 ArtifactManifest 才能跨任务使用成果。

完成阶段 A 后，运行目标测试和全量 Python 3 测试；修复全部失败再继续。

### 阶段 B：ModelRuntime、缓存与 Pydantic 迁移

1. 引入 `pydantic` 依赖，锁定精确版本，在 Windows + Python 3.11 实测 `pip install`、`import`、最小端到端跑通三项。
2. 核心合同从 frozen dataclass 迁移到 Pydantic v2 `BaseModel`：`frozen=True`、`extra='forbid'`、`validate_assignment=True`。`__post_init__` 手写校验删除，改用 `Field` 约束和 `@model_validator`。
3. 模型输出验证用 `model_validate` 替代手写校验链；`tools` JSON Schema 用 `model_json_schema()` 自动生成。
4. `digest()` 规范哈希输入改用 `model_dump(mode='json', exclude={'digest'})`，保证与 Pydantic 序列化口径一致。
5. 所有模型调用收口到 `ModelRuntime.invoke()`。
6. 建立规范 Prompt 渲染：稳定合同、角色、能力索引在前，动态上下文和请求在后；禁止在稳定前缀中加入时间戳、run_id、session_id、随机路径和无序 JSON。
7. 精确缓存键必须包含机构/安全范围、provider/model、角色、Prompt 版本、输入、工具合同、能力、上下文 Projection、业务规则和生成参数哈希。
8. 只缓存 `succeeded + schema_validated`。失败、额度、协议错误和 uncertain 不能复用。
9. 实现相同 call_key 的 single-flight。
10. 崩溃后成功记录直接复用；uncertain 不自动重试。
11. 建立 ProviderConnection、ModelBinding、AgentModelPlan、TokenPlan、ProviderRegistry 和 ProviderAdapter 的严格合同。当前仅安装 MiniMax Adapter，测试使用 Fake Adapter；删除旧规划代码中的直接 provider 调用、自动选模和 fallback。
12. ModelRuntime 的 `invoke()` 支持流式输出接口（§14）：支持流式的 Adapter 启用 `stream=True`，逐 token 推送 `model.token` 事件；Fake Adapter 静默跳过。

必须增加以下接口测试：完全相同调用零第二次模型请求；任一哈希变化 miss；并发相同调用只有一次 Adapter 调用；跨机构不命中；失败/额度/uncertain 不命中；重启后成功记录复用；Pydantic `ValidationError` 带字段路径；`model_json_schema()` 与验证器同源不漂移。

### 阶段 C：TaskCompiler 和 WorkflowEngine（LangGraph）

1. 引入 `langgraph` 依赖，锁定精确版本，在 Windows + Python 3.11 实测 `pip install`、`import`、最小端到端跑通三项。
2. 把 `task_contract.py`、`semantic_domain.py` 和相关上下文证据逻辑收进 TaskCompiler。
3. 把 `planning_engine.py`、`planning_state_machine.py`、WorkflowVerifier、EvidenceResolver、DominanceGate 和能力闭包逻辑收进 WorkflowEngine 的深接口。
4. WorkflowEngine 规划状态机用 LangGraph `StateGraph` 实现（§13.2）：草案、确定性验证、有界修复、G3 审计、必要修订、最终验证、封存声明为图节点和条件边。每个节点是纯函数，接收 `RunState` 返回部分更新。
5. 检查点用 LangGraph `SqliteSaver` 持久化到 JournalStore SQLite；`thread_id = run_id`。
6. `authorization_required` 暂停态用 `interrupt_before` 实现；`GeoPilotKernel.decide()` 用 `graph.invoke(None, config)` 恢复。
7. 模型只能提交草案；服务器绑定实体、推导 output_format、选择状态、图层类型和默认输出规则。
8. G3 不能改写用户目标、增加未要求成果、放宽验证或自行判定正确。
9. 生产 G3 必须复用已编译 IntentSpec；不要无条件重复语义模型调用。
10. LangGraph `stream_mode="updates"` 产出节点级事件，推送到 SSE 通道（§14）。
11. LangGraph retry 关闭或限于网络类错误；`QuotaStopped` 和 `ModelCallUncertain` 不自动重试。修复循环上限由 `recursion_limit` 控制。
12. 新深模块接口测试覆盖后删除旧公开类、旧导入和测试，不保留包装器。

必须增加以下接口测试：LangGraph 检查点恢复不重复已提交节点；`interrupt_before` 暂停后 `decide()` 从正确节点恢复；`recursion_limit` 达上限进入 `ContractFailed`；LangGraph checkpoint 与 JournalStore `run_events` 阶段一致；`stream_mode="updates"` 推送正确节点事件。

### 阶段 D：PolicyGate、RuntimeLease、验收、发布与增量上下文

1. 授权绑定 actor、权限范围、run_id、plan_hash、输入输出身份、风险等级、lease 和过期时间。
2. ArcMapRuntime 必须精确绑定 arcmap_pid、bridge_pid、bridge_port、HWND、deployment_hash、lease_id 和 epoch。
3. 删除扫描任意端口、选择第一个健康 Bridge、Bridge 死亡后静默切换或自动重启的链路。
4. 回调强制校验 lease_id + epoch + plan_hash；重复和过期回调拒绝。
5. 执行前重新核对上下文哈希。上下文变化时拒绝旧计划。
6. 数据写操作进入 staging；运行结果经独立 AcceptancePublisher 验收后才发布。
7. 分发后连接中断必须通过 outbox/receipt reconcile；无法确定则 `ExecutionIndeterminate`，禁止自动重放。
8. 实现增量上下文捕获（§4.2）：结构层（图层引用、字段名、坐标系、几何类型、选择计数）全量捕获，值摘要层惰性（TaskCompiler 判定需要时才采样）。执行后复核只捕获 workflow 声明的输出图层。
9. 执行等待由 ArcMap 权威回执事件驱动；30s 心跳只证明租约存活，不推断执行结果，也不形成第二条完成路径。

### 阶段 E：能力、安全、流式输出与 UI 切换

1. 每个 Capability 补齐输入输出、坐标系/单位/几何、前后置条件、语义效果、风险、幂等性、ArcGIS 依赖、验收器和部署哈希。
2. 普通模型先看稳定精简能力索引，服务器确定闭包后才提供少量完整能力卡。
3. 从生产 Agent 工具集中删除 ToolBuilder。将能力开发移到独立管理员工具；未经审核、测试、签名和部署的 executor 不能加载。
4. 删除 wildcard CORS，增加固定 Origin、会话令牌、CSRF/RBAC 和敏感数据出站白名单。
5. HTTP 路由、Web UI、外部 Agent 只能调用 GeoPilotKernel，不得访问 store、planner、provider 或 ArcMap client。
6. EventBus 从 `run_events` 事实表投影事件（§14），不再只依赖内存历史；SSE 断线重连用 `Last-Event-ID` 从事实表补发，不丢事件。
7. 前端移除 `waitForRun` 退避轮询，纯靠 SSE 驱动 UI 更新；收到 `run.stage_changed` 时只拉取变化的 run，不全量拉取 50 条。
8. 前端模型等待 UI 从假计时器改为真实阶段展示（§14.4）：`received -> context_frozen -> intent_compiled -> plan_verified -> authorized -> executing -> succeeded`。

### 阶段 F：清理与离线总验收

彻底删除：

- 旧规划和状态机文件
- 旧 RunController/GatewayState 业务链路
- 旧数据库合同
- Bridge 端口扫描和静默拉起
- 规划供应商 fallback/自动选模
- 生产 ToolBuilder 链路
- 旧字段映射、兼容别名、死代码和无效测试
- 手写 `__post_init__` 校验和手写 JSON Schema（已被 Pydantic v2 替代）
- 手写 `PlanningStateMachine` 状态转换表（已被 LangGraph `StateGraph` 替代）
- `context_reader.py` 的值样本全量扫描逻辑（已被增量捕获替代）
- `web/app.js` 的 `waitForRun` 退避轮询和假计时器（已被 SSE 流式输出替代）
- `event_bus.py` 的内存 200 条历史（已被 `run_events` 事实表投影替代）

此阶段仍不得启动实验。`run_ablation_campaign.py` 和 `run_formal_experiments.py` 的替换放在生产架构离线验收全部通过之后；若当前改动已经使其成为死链路，可删除，但不得运行。

## 测试要求

测试以深模块接口的可观察结果为准，不测试内部实现细节。新接口测试建立后删除旧浅模块测试，不叠加两套测试。

至少覆盖：

- 新 TaskSession 零历史上下文污染。
- 显式导入成果及来源审计。
- 精确缓存、single-flight、缓存失效和租户隔离。
- 每个状态转换后进程退出的恢复。
- 任意角色绑定的 Provider 额度停止且不调用第二连接。
- Bridge 死亡、旧 epoch、旧 plan_hash、重复回调和上下文漂移。
- 执行成功但成果缺失时验收失败且不发布。
- 未授权副作用拒绝。
- 所有 Capability 的合同与 executor 一致。
- Python 2.7 兼容和 Unicode 路径。
- Pydantic `BaseModel` 构造非法输入抛 `ValidationError` 并带字段路径；`model_json_schema()` 与验证器同源不漂移；frozen + `extra='forbid'` + `validate_assignment=True` 生效。
- LangGraph 检查点恢复不重复已提交节点；`interrupt_before` 暂停后 `decide()` 从正确节点恢复；`recursion_limit` 达上限进入 `ContractFailed`；LangGraph retry 不自动重试 `QuotaStopped` 和 `ModelCallUncertain`。
- 增量上下文：只读查询不触发值摘要采样；属性过滤类请求按需采样指定字段；执行后复核只捕获声明输出图层。
- 流式输出：SSE 事件从 `run_events` 事实表投影；断线重连后从 `Last-Event-ID` 补发不丢事件；前端无退避轮询请求；非 streaming Adapter 不阻塞。

常规验证至少包括：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q gateway_py3 agent_integrations\geopilot-arcmap\scripts tests
node --check gateway_py3\web\app.js gateway_py3\web\app_render.js gateway_py3\web\app_voice.js
git diff --check
```

先确认当前 Web 文件清单，再对所有实际 JavaScript 文件执行 `node --check`。ArcMap Python 2.7 测试必须使用仓库现有的 ArcGIS Python 2 运行方式；不要用 Python 3 通过来代替 Python 2 证明。不要执行打包和安装。

## 工作纪律

- 持续完成当前阶段的实现、测试、代码审查和死代码清理，不要只交一份新计划。
- 每次改动后从第一性原理检查：事实由谁拥有、谁有权改变状态、失败是否可见、恢复是否会重复副作用、是否存在更简单的深接口。
- 遇到测试失败先定位根因，不通过兼容分支或放宽断言让测试变绿。
- 遇到真实核心决策分叉才询问用户；普通实现选择自主完成。
- 最终报告只说明完成了哪些阶段、测试通过/失败数量、仍有哪些真实阻塞。不得把静态检查描述成运行时验证，不得声称没有执行的实验已经完成。

现在从检查工作树、阅读目标架构和现有入口开始，依次完成阶段 A 到阶段 F。每个阶段完整通过后继续下一阶段，除非出现目标架构无法裁决的核心分叉；整个过程不要启动任何真实模型或实验。
