# GeoPilot Bridge Lease 协议（阶段 E 三端契约）

> 状态：实施基线
> 范围：gateway（Py3）↔ C# Bridge（ArcMap 进程内）↔ Py2 runtime（ArcMap 内）三端共享的
> 执行协议。本协议替换旧的 claim/heartbeat/complete/context 协议（v1）。
> 旧协议端点随旧网关删除，不保留兼容（目标架构 §2）。

## 1. 设计原则

- **单一 fencing 身份**：一切执行、心跳、回执、上下文回调都携带
  `lease_id + epoch + plan_hash` 三元组。任一不匹配即拒绝（§6.7）。
- **lease 由 gateway 签发，Bridge 只转发**：gateway 的 ArcMapRuntime.acquire 生成 lease；
  C# Bridge 把它原样传给 Py2 runtime；Py2 只认 lease，不自行 claim。
- **回执是唯一事实**：Py2 执行完成后回传 receipt；gateway 只有收到权威 receipt
  才推进到 executed。无法证明 → ExecutionIndeterminate，禁止重放（§2.5）。
- **上下文捕获走 lease**：sync-context 改为携带 lease_id，context 回调 fencing。

## 2. 身份字段

所有请求/回执携带：

```json
{
  "lease_id": "UUID",
  "epoch": 1,
  "plan_hash": "sha256",
  "run_id": "UUID"
}
```

- `lease_id`：gateway `ArcMapRuntime.acquire` 生成（UUID）。
- `epoch`：每次重新 acquire 递增，从 1 开始。旧 epoch 的回执/心跳拒绝。
- `plan_hash`：封存 VerifiedPlan 的 digest。
- `run_id`：kernel run 的 UUID（= LangGraph thread_id）。

## 3. 端点契约

### 3.1 gateway → Bridge（C# Bridge 监听）

| 端点 | 方法 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| `/health` | GET | - | `{ok, bridge_pid, bridge_port, summary{targets[]}}` | 不变，用于目标发现 |
| `/dispatch` | POST | `{lease_id, epoch, plan_hash, run_id, allow_edits, context_snapshot}` | `{ok, run_id}` | 替换旧 `/runs/:id/execute`。Bridge 把 lease 三元组 + context 写 silent command 文件，触发 Py2 执行 |
| `/capture-context` | POST | `{lease_id, epoch, run_id, phase}` | `{ok, run_id}` | 替换旧 `/sync-context`。Bridge 触发 Py2 读 ArcMap 上下文并回调 gateway |

### 3.2 Bridge → gateway（Py2 runtime 回调）

| 端点 | 方法 | 请求 | 说明 |
|---|---|---|---|
| `/runs/:id/receipt` | POST | `{lease_id, epoch, plan_hash, status: executed\|failed, result, result_hash}` | 替换旧 `/complete`。fencing 校验后写 execution_receipts |
| `/runs/:id/heartbeat` | POST | `{lease_id, epoch, plan_hash}` | 替换旧 heartbeat（旧带 owner_id，新带 lease 三元组） |
| `/runs/:id/context` | POST | `{lease_id, epoch, plan_hash, phase, context}` | 上下文回调（旧带 sync_token，新带 lease 三元组） |
| `/runs/:id/reconcile` | POST | `{lease_id, epoch, plan_hash}` | Bridge/Py2 主动查询执行状态（分发中断后） |

### 3.3 Bridge 内部（C# ↔ Py2，silent command file）

`bridge_command.json` 字段改为：

```json
{
  "action": "execute" | "sync",
  "lease_id": "UUID",
  "epoch": 1,
  "plan_hash": "sha256",
  "run_id": "UUID",
  "allow_edits": false,
  "context": {...},
  "target": {"bridge_pid": 0, "bridge_port": 0, "arcmap_pid": 0, "hwnd": 0},
  "expires_at": 1234567890.0
}
```

## 4. 执行时序

```
gateway                          C# Bridge                    Py2 runtime
  │ acquire→lease                  │                              │
  │──/dispatch(lease)─────────────▶│──write bridge_command.json──▶│
  │                                │──item.Execute()─────────────▶│ (ArcMap UI 线程)
  │                                │                              │──read context
  │                                │                              │──execute workflow
  │                                │                              │──/runs/:id/receipt(lease)
  │◀──receipt─────────────────────│                              │
  │ 校验 lease+epoch+plan_hash      │                              │
  │→ executed→accepted→published    │                              │
```

- Py2 执行期间每 5s 发一次 `/runs/:id/heartbeat`（带 lease 三元组），gateway 更新
  `runtime_leases.last_heartbeat`。
- 分发后 gateway 连接中断 → Bridge 侧 `/runs/:id/reconcile` 查询 → 无法证明则
  ExecutionIndeterminate。

## 5. 错误语义

- 任一 fencing 字段不匹配：HTTP 403 + `{error: "lease fencing mismatch"}`。
- Py2 执行失败：receipt status=failed + result 含 error/traceback。
- Bridge 等待超时（30s）：`{ok:false, error:"Bridge request wait expired"}`，gateway
  侧进入 reconcile，不自动重放。

## 6. 删除项（随旧网关）

- `/runs/:id/claim`、旧 `/runs/:id/heartbeat`（owner_id 语义）、旧 `/runs/:id/complete`、
  `/sync-context`（旧 sync_token 语义）、`/runs/:id/execution-state`（旧状态轮询）。
- Py2 的 `_claim_run`、`owner_id` 心跳身份、`sync_token` 上下文握手。
