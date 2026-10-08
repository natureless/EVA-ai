# EVT-00：接纳请求与终态回执

状态：代码实现与隔离验收，2026-09-12。对应 [v3 开发计划](source-review-and-roadmap-v3.md) 的 EVT-00。

本批保证当前进程中的聊天请求不会因队列淘汰或结果清理而静默消失。已接纳请求可查询等待状态或终态回执；结果保留期结束后明确返回不存在，不再无限返回“处理中”。这不是跨重启的耐久接纳协议。

2026-10-01 补充：[ACT-02](durable-requests-act02.md) 增加默认关闭的耐久请求
接纳、事务领取和重启终态查询。以下进程内边界仍描述默认路径；启用该功能时
适用 ACT-02 的原期限/保留期、来源冲突与无重放恢复规则。

## 已复现的问题

[修复前记录](../artifacts/evt00-before.json) 记录了四条路径：容量为 1 时后台 tick 淘汰聊天请求；清理按创建时间删除仍在等待的旧请求；迟到结果覆盖已有终态；未知任务的轮询返回 202。

其中清理复现使用修改前的 ResultRegistry，人工将等待项的创建时间设为 120 秒前，再执行 60 秒 TTL 清理。首次以同一时钟瞬间调用 `ttl=0` 未稳定复现，记录已保留这一限制，未把该次结果当成成功复现。

## 接纳、执行与终态责任

| 场景 | 负责组件 | 对外结果 |
| --- | --- | --- |
| EventBus 或结果登记容量已满 | HTTP 接纳入口 | 503、`accepted=false`，不发布请求；不删除其他请求的记录 |
| 运行时关闭或消费器不可用 | 接纳入口 / RuntimeController | 503，分别为 `runtime_stopping` / `runtime_unavailable` |
| EventBus 满且存在可替换维护事件 | EventBus | 仅替换无 correlation ID 的 `system_tick` 或 `maintenance`，计数可见 |
| 已接纳的聊天、GitHub、提醒或带 correlation ID 的事件 | EventBus | 不作为淘汰候选；队列没有可替换项时拒绝新输入 |
| Minimal 内部队列满或等待超时 | Minimal → EventProcessor | `cognitive_queue_full` / `cognitive_queue_timeout` 终态回执 |
| HTTP 同步等待先到期 | sync 路由 | 202、`wait_timed_out=true`；请求仍可能等待或运行 |
| 请求期限到达，尚未开始执行 | ResultRegistry → EventProcessor | `expired`、`request_deadline_exceeded`、`execution_state=not_started`；以后取出时也不执行 |
| 请求期限到达，已经开始执行 | ResultRegistry | `outcome_unknown`；不宣称线程、工具或外部副作用已停止 |
| 停机时尚未开始的登记请求 | EventProcessor.request_stop | 立即生成 `rejected` 回执；无需等被阻塞的 worker 退出 |
| 执行成功、失败或拒绝 | EventProcessor | 写入首个终态后广播；广播失败仍可轮询 |
| 终态保留期结束、未知 ID 或进程已重启 | 查询路由 | 404、`result_unknown_or_expired`；无法仅凭内存注册表区分这些原因 |

队列容量判定、替换和任务计数在同一个队列锁中完成，幸存事件保持 FIFO。满队列寻找可替换项的成本为 O(N)。这是容量保护，不是新的优先级调度器。`dropped` 保留兼容计数，同时拆出 `evicted_background`、`rejected_full`。

## 回执生命周期

[ResultRegistry](../runtime/result_registry.py) 使用单调时钟，登记时保留事件 ID。执行服务在任何模型、记忆或工具调用前认领登记项；终态项不能再次执行，错误事件 ID 不能替换已登记的原事件。

同一个登记项的首个终态不可被覆盖。重复登记不会替换原等待对象，迟到的成功结果也不会抹掉超时回执；读取返回副本。HTTP 同步和轮询不再 `pop`，多个客户端可重复读取。

期限在查询、等待、认领、交付及清理时检查；没有新增独立定时线程。即使消费器停止前未给出回执，下次查询也能产生期限终态。清理只删除保留期已过的终态，不能根据请求年龄删除尚未到期的等待项。新生成的过期回执从生成时开始获得保留窗口。

普通登记与计数访问最多每秒触发一次全量清理；具体任务的查询和交付仍即时检查自身期限。显式诊断清理可缩短终态保留期，但不删除未到期的等待请求。

| 配置 | 默认值 | 含义 |
| --- | ---: | --- |
| `EVA_REQUEST_TIMEOUT_SEC` | 8 秒 | 同步 HTTP 等待窗口，不是执行取消期限 |
| `EVA_RESULT_PENDING_TIMEOUT_SEC` | 300 秒 | 登记到终态的最长请求期限 |
| `EVA_RESULT_TTL_SEC` | 60 秒 | 终态生成后的可查询保留期 |
| `EVA_RESULT_REGISTRY_CAPACITY` | 20,000 项 | 等待项和保留终态的合计上限；达到后拒绝新聊天请求 |

过期回执不会立即把原事件从 EventBus 或 Minimal 队列中删除；队列所有者以后消费或清理该项时完成队列确认。容量保护不能代替修复失效的消费器。

## API 和网页

`GET /api/chat/result/{task_id}`：等待/运行返回 202；有终态返回 200；无保留项返回 404。返回 `terminal_state`，可为 `succeeded`、`failed`、`rejected`、`expired`、`outcome_unknown`。`completed=true` 表示服务已有终态回执，不表示底层工作已停止，也不证明业务目标达成。

`POST /api/chat/stream` 从同一注册表读取最终经过审查的文本，不依赖 WebSocket 订阅。响应头 `X-EVA-Task-ID` 用于后续查询；先发送原有文本 `data:` 帧，再发送携带完整回执的 `event: result` JSON 帧，最后发送 `[DONE]`。断开流不会自动重发或取消已接纳请求。自定义 SSE 客户端需按 `event:` 类型解析，不能把回执 JSON 拼进聊天正文。

网页已适配命名回执帧，能显示队列/登记容量拒绝。非流式模式在收到接纳确认后立即轮询，补救“WebSocket 回复先于确认返回”及 WebSocket 丢失；轮询不会重新提交行动。网络持续不可用时结束本地等待并显示结果不确定，不伪造服务端失败回执。消息按请求关联，避免回执更新到其他消息上。

`/api/state` 新增 `result_registry` 计数，区分等待和保留终态；旧 `pending_results` 字段仍表示注册表总项数，保留兼容性。

## 验证

[请求回执测试](../tests/test_request_receipts.py) 覆盖保护事件、维护替换、FIFO/任务计数、并发发布、期限与保留、竞争完成、重复登记、事件身份、容量、HTTP 和无消费器 SSE。

[执行服务测试](../tests/test_event_processor.py) 覆盖过期请求不写记忆、不调用 Agent，广播失败不改回执，以及 worker 取消/超时/故障的未知结果表述。负载矩阵为 legacy / Minimal × 正常完成 / 执行中关闭；每组先阻塞一个实际测试 Agent，再由 8 个发布线程提交 500 个请求，逐项检查已接纳请求都有终态、可重复读取、队列计数归零。测试使用确定性本地 Agent，不是生产吞吐量或真实 LLM 质量测量。

Minimal 的关闭可能在底层辅助线程尚未退出时返回 `False`；测试在释放 Agent 后重试关闭，验证依赖保留和最终退出，没有将“收到取消回执”当作真实线程已退出。

[网页处理测试](../tests/js/test_chat_receipts.cjs) 通过 Node 内置测试运行器执行实际浏览器脚本，验证分块 UTF-8/SSE 回执不会混入正文、HTTP 拒绝、快速回复恢复和未知回执结束等待。运行命令：

```powershell
node --test tests/js/test_chat_receipts.cjs
python -m pytest -q --tb=short --show-capture=no -W error::pytest.PytestUnhandledThreadExceptionWarning
```

完整复测：**1,240 passed in 115.42s**，未处理线程异常按失败处理，无警告；四个默认数据文件前后 SHA-256 均不变，见 [验证记录](../artifacts/evt00-validation.json)。首次完整回归 1,236 项通过；随后补齐未知结果文本及 4 个针对性用例，再进行上述全量复测。Python 改动经过 Ruff，网页脚本通过语法与 4 项行为测试。本批没有启动生产服务、迁移默认数据库或进行浏览器视觉验收。

## 仍保留的边界

回执与队列仍在当前进程内；S5 写入仍是尽力记录，重启不能恢复聊天回执。尚未提供持久 inbox/outbox、跨重启去重、动作对账或通用 exactly-once 保证。开始执行后的未知结果不会自动重试。历史回执、结果撤销和业务目标完成证据需由后续持久化与目标系统承担。

下一项 BASE-02 将核对默认、Minimal、MVSC 的真实装配入口和 UI 模式标识，建立固定离线数据与运行基线。
