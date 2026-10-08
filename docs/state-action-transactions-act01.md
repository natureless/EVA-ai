# ACT-01：状态提交与行动意图事务

2026-10-01 已实现 SQLite 权威状态仓库和行动 outbox，并接入可显式调用的
`PipelineCognitionLoop` / `create_adapted_loop`。ACT-01 阶段默认装配与 HTTP 消费者
仍使用原路径。后续 [ACT-02](durable-requests-act02.md) 已将独立请求派发账本接到
实际 legacy/Minimal 消费者，默认关闭；世界/记忆事务与补偿仍在计划内。

## 原子边界

`SQLiteStateRepository` 保留 `load`、`commit`、`commit_delta`、`replay` 契约，
增加 `TransactionalStateRepository.commit_with_actions`。一次 SQLite 事务内：

1. 检查当前 revision 与预期 revision 相同，新状态必须是 `n + 1`。
2. 校验完整状态、有限 JSON、主体和内容哈希。
3. 保存新状态、对应 revision 的源事件历史。
4. 保存绑定该状态 revision 和源事件的行动意图，初始状态为 pending。

状态、事件与意图中任一写入失败，三者都回滚。不在此事务里执行外部动作。
`commit_delta` 在同一事务内读取、应用和提交，两个同基线写者只有一个成功。
异步方法将 SQLite 工作放到线程中；读取均返回重新校验的独立对象。

表为 `cognitive_states`、`cognitive_state_commits`、`cognitive_action_outbox`，
独立于现有 EVT-02 表。新表有 schema v1 校验；状态继续使用现有
`ConsciousState` 的内容哈希规则。哈希是完整性检查，不是来源或安全认证。
不会把旧快照或世界图隐式迁移为新仓库的权威状态。

单个状态最多 256 KiB，单个事件最多 64 KiB，每次提交最多 64 个事件、
事件集合最多 1 MiB、最多 16 个行动。行动参数最多 32 KiB，完整意图/收据最多
64 KiB。history 按完整 revision 分页，每页最多 100 个；replay 的范围超过
100 个 revision 时明确拒绝，要求分页，不静默截断事件。

## 行动契约与派发

`ActionIntent` 固定 action ID、source event ID、slot、tool ID 和有限参数。
意图哈希覆盖这些字段；`(subject_id, source_event_id, slot)` 唯一，重用 action ID
或同一源事件的相同 slot 会回滚新状态提交。参数用于恢复实际任务，保存于调用方
指定的私有 SQLite 库；没有新增公开参数读取或任意工具执行 API。

`ActionDispatcher` 在调用注入的处理器前领取 pending 动作并耐久保存随机领取
凭证和开始时间。处理器收到提交时的意图与状态副本，必须执行当前授权/边界检查；
意图存在不授予执行权限。处理器 I/O 不持有 SQLite 写事务。

| 存储状态 | 含义 | 重启行为 |
| --- | --- | --- |
| pending | 意图已提交，尚未领取 | 可显式派发，不重新计算参数 |
| executing | 已领取，可能已有副作用 | 上一运行实例和 worker 已退出后封存为 unknown |
| completed | 观察到处理器明确返回 `ok=true` | 保留，不重放；不是业务成功证明 |
| unknown | 错误/异常返回，或领取后中断 | 保留，不自动重试、不因晚到返回升级 |

只有 matching claim token 的 executing 动作可以记录首次返回。领取写失败时
不调用处理器；副作用后的返回记录失败保留 executing，由恢复封存未知。
没有租约超时重放，也没有通用补偿或外部幂等承诺。

`recover_actions(limit=256)` 是启动期批量操作，必须先确认上一运行实例及实际
worker 已退出；可重复调用直到本轮返回 0。它不执行动作，且拒绝当前仓库仍有
活动 dispatcher 时恢复。跨实例写者的 revision/claim 竞争已有测试，生产恢复
仍要求单一活动运行实例，不能把恢复接口当作抢占活跃 worker 的机制。
活动 dispatcher 会阻止其仓库关闭；调用者仍须管理其处理器内部真实 worker。

## 已接入的实验循环

构造时同时注入同一仓库的 `state_repo` 和 `action_dispatcher` 才启用事务行动
路径。在 ACT 前提交 world/body/self/evaluation 的计划状态及 `cognition_act`
意图，然后执行已有 `_execute_action` 钩子。记录的是整个 ACT 调用组，不能
宣称每个嵌套工具已有独立派发/幂等协议。

反馈是第二个 revision，不重复计 tick 或重复应用前面的增量。处理器返回只形成
调用观察；源事件相同的再次 `run_once` 只返回已存状态，不重新规划或执行。
相同源事件 ID 携带不同内容时拒绝。反馈提交失败后，已存意图/收据仍阻止动作
重做。仓库存在时每次读取都以最新耐久状态为准；内存缓存不覆盖其他提交。

`dispatch_pending_action(action_id)` 是明确的待派发恢复入口，仅执行已存意图，
不自动继续整个旧 tick 的验证、记忆整合或反馈提交。调用者应读取真实收据与历史，
不能把这个入口返回的处理器结果当作旧 tick 的业务完成证明。
`create_adapted_loop` 支持同样的显式依赖注入，默认 bootstrap 不启用。

世界模型、记忆、预测与自我模块在其他阶段既有的副作用仍在该事务之外。
这次实现不能宣称全 EVA 状态与所有动作已原子化，也不把 MVSC 实验循环升级为
当前 HTTP 消费者。实际消费者接入需要持久接纳、授权/期限复查、领取与回复/目标
关联、生命周期和恢复调度的完整接线。

## 验证与下一步

测试覆盖状态协议、跨连接 revision 竞争、三张表任一插入失败回滚、源事件/主体/
slot 校验、状态和收据格式/大小拒绝、状态历史分页、领取凭证与首个终态保护、
双 dispatcher 单次调用、活动派发期间关闭保护，以及真实文件 Executor：
未领取前退出、领取后写入前退出、文件写入后退出三个进程窗口。

现有循环集成测试验证先提交计划状态后才写文件、反馈 revision 不重复 tick、
反馈提交失败不重复动作，以及失败领取后显式派发固定意图。

2026-10-01 Windows 全量 Python **1842 passed, 1 skipped in 179.05s**，
全量 JavaScript **385 passed**。本轮新增状态/行动测试 **35 passed**；全项目
Ruff、10 个本轮 Python 文件格式检查、7 个生产文件 mypy
（`--follow-imports=silent --disable-error-code=import-untyped`）通过。
检查前后没有安装 Black 或声称全项目类型检查通过。

启动预检仅因 8000 端口占用失败，其余检查通过；本轮改动的 `git diff --check`
通过。没有新增浏览器视觉、真实远程副作用、系统断电、容器或生产切换验收。

ACT-01 原下一入口的实际请求接纳、领取与回复/目标关联已推进至
[ACT-02](durable-requests-act02.md)。未领取请求的恢复调度、未知结果独立对账
和补偿决策仍待推进；REC-01 的文件样本不会自动解除未知领取的重试限制。
