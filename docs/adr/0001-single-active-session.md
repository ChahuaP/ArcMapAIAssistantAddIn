# ADR 0001：全系统唯一活动会话

状态：已采纳。

GeoPilot 在单机上只有一个 `ActiveSession`。服务端 SQLite 的单行 `active_session` 是唯一权威；浏览器、自动化和 Add-in 只读取它，不能生成、恢复或选择活动会话。`GET /api/v1/active-session` 幂等且首次原子创建；`POST /api/v1/active-session/clear` 在同一事务中归档旧会话、创建新会话并递增 `SessionEpoch`。

不可逆取舍：不再支持多浏览器、多窗口或旧 UUID 并行对话。它们必须共享同一个活动会话；清空后旧会话只能通过归档列表只读管理，不能进入当前模型上下文。所有写入、run 控制和 SSE 都必须携带当前 session id 与 epoch；不匹配一律返回 409 `ContractFailed`，不提供默认会话、旧 UUID 或回退逻辑。
