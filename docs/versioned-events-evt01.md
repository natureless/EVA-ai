# EVT-01：版本化事件契约与兼容适配

2026-09-12 至 2026-09-13。此项把事件版本、来源标识和格式转换接入现有消费链路；没有替换消费者或提供持久 inbox/outbox。

## 契约归属与实际接线

通用 `EventEnvelope`、`EventMetadata` 和 `EventFamily` 位于 [event/contracts.py](../event/contracts.py)。`packages/contracts/events.py` 保留兼容导入，引用的是同一个类。稳定运行时代码不需要导入实验包。

现有消费者仍使用 [Event](../event/event_schema.py) 的 `id/type` 短字段；它与 envelope 共享版本和元数据定义。[codec](../event/codec.py) 明确映射 `id ↔ event_id`、`type ↔ event_type`，同时保留来源、时间、关联、因果引用、主体、会话、来源事件标识、优先级及其他已声明元数据。缺失关联 ID 保持 null，不再随机生成一个关联。

| 稳定短类型 | Envelope 类型 |
| --- | --- |
| user_message | perception.user_message_received |
| system_tick | perception.scheduler_tick |
| reminder_trigger | perception.system_event |
| maintenance | lifecycle.maintenance_started |
| github_push | perception.github_push |
| github_pr | perception.github_pr |
| github_issue | perception.github_issue |
| github_workflow | perception.github_workflow |

历史 `legacy.github_*` 名称有显式兼容映射，原名称记录在 compatibility 中。其他实验事件可由 MVSC 实验组件使用，但没有稳定消费者映射时，不允许通过适配器进入稳定队列。原来“未知类型转 system_tick”的行为已删除；未知 GitHub webhook 也不再当作 push。

MVSC 开关仍只装配适配器，HTTP 消费者仍是 legacy。Minimal 与默认配置继续使用同一个 EventProcessor；实际选择见 [BASE-02](runtime-baseline-base02.md)。

## 版本与校验边界

当前唯一可接纳的显式版本是字符串 `"1.0"`。数字 1、null、未知版本均拒绝。原 MVSC 信封的兼容字段保留；新增字段有默认值，序列化契约使用明确版本。

```json
{
  "schema_version": "1.0",
  "event_id": "retained-event-id",
  "event_type": "perception.user_message_received",
  "source": "user",
  "timestamp": "2026-09-12T00:00:00Z",
  "source_event_id": "client-message-42",
  "correlation_id": "http-request-receipt-id",
  "payload": {"text": "hello", "remember": false}
}
```

新事件时间必须包含时区，并规范化为 UTC；payload/compatibility 必须可序列化为有限的 JSON 值。信封不接受未声明的顶层字段。字段校验不验证事实真伪；confidence 是生产者元数据，不能替代证据核实。

EventBus 在入队及写库前重新验证，并持有调用方事件的独立快照。之后修改调用方的嵌套 payload 或关联 ID，不会改变已接纳事件。SQL 写入参数在入队前准备，避免消费者与持久化线程共享可变字段。EventProcessor 的直接调用也有校验，非法契约通过失败回执拒绝。队列消费对象本身仍可由消费者修改；这不是所有对象都不可变的系统。

MVSC adapter 在写入 EventStore 或通知订阅者之前检查版本和可映射类型。EventStore 单条与批量追加也重新验证版本，读取时核对版本、身份、时间、payload 等投影与保存的契约是否一致。

## 来源标识的含义

稳定事件 ID 由版本固定的 UUIDv5 算法，对主体、source、短类型、source_event_id 的结构化组合生成。算法使用字段边界明确的 JSON 编码，避免字符串拼接歧义。

| 来源 | occurrence 的定义 | 限制 |
| --- | --- | --- |
| HTTP 对话 | 可选 `source_event_id`，由调用方标识同一次消息 | 未提供时每次调用生成新 ID；同文消息不会自动合并 |
| GitHub webhook | `webhook:` 加已验签请求的 delivery ID，再按仓库 source/type 分域 | 载荷不能覆盖 `_delivery_id`、`_github_event` 等规范化字段 |
| GitHub poll | `poll:` 加种类和排序后完整 item JSON 的 SHA256 | 相同内容再次观测 ID 相同，内容变化则不同；不是 webhook delivery 的同一身份 |
| scheduler | 当前调度器实例的 run ID、事件种类、观测时间 | 同一实例和观测时间可重建身份；跨重启不是同一持久调度槽 |

事件 ID 标识来源 occurrence；correlation_id 标识本次 HTTP 等待/回执。**稳定 ID 不等于幂等执行。** 重复提交相同 `source_event_id` 仍可能进入多个处理调用，调用方不能把 ID 稳定解释为执行只发生一次。来源标识不得复用于不同消息；冲突检测、持久去重与动作对账属于 EVT-02。

定时任务兼容载荷字段 `scheduled_at` 当前记录回调观测时间，不代表 APScheduler 原计划的触发时刻。历史未知来源 ID 不根据消息正文或不可信的旧 payload 元数据反向捏造。

## 存储与历史读取

[迁移 008](../migrations/008_event_contract.sql) 只给稳定 events 表添加 nullable `event_contract` 文本列。新 EventBus/MemoryAPI 写入完整 envelope JSON，旧列保留，事件时间使用原事件时间，修正了 EventBus 以前用写入时间覆盖事件时间的问题。捕获记录仍为同步、尽力写入，不构成持久接纳承诺。

`decode_event` 和 `MemoryAPI.get_event(id)` 支持：

- 显式 v1 envelope，以及 v1 稳定 Event 格式。
- `event_contract IS NULL` 的旧稳定行；必须有原 ID、类型、来源和时间，读取时不生成新 ID 或当前时间。
- 旧行的无时区时间按历史 UTC 约定解释，并保留 `compatibility.timestamp_assumed_utc=true`；该假设不用于新 v1 事件。

存在非空 event_contract 时，版本未知、JSON 损坏或与旧列冲突都报错，不回退到旧列掩盖损坏。旧行的 `status` 捕获/归档投影与源事件状态不同，不作为来源身份校验项。对话归档导入仍可保留旧行；例如不属于可消费类型的归档记录不能因此被转成 tick 执行。

实验 EventStore 也添加 nullable event_contract，保留完整来源元数据。其旧 correlation_id 列要求非空，缺失关联在该 SQL 投影中用空字符串表示，完整 envelope 中仍为 null；读取旧投影时恢复为 null。sequence 由该 EventStore 分配，是本地日志序号，不是跨数据库的全局次序。

## 离线副本演练

```powershell
python scripts/migrate_event_contracts.py --source data/eva.db --output data/event-contracts/new-copy.db --report artifacts/event-migration.json
```

[迁移工具](../scripts/migrate_event_contracts.py) 用 SQLite backup 创建新副本，拒绝覆盖已有目标或源文件。只在副本应用迁移及分批补齐 envelope；默认每批 500 条。既有有效契约只验证、不重写；无效或不支持的记录保留原样并计入报告，报告标为 partial。不存在事件重放、模型或工具调用。

本次对默认数据库创建独立副本，**12,973 条历史事件全部补齐并读回通过**。对原 7 个列按行计算摘要，确认 ID、类型、来源、时间、payload、correlation_id、status 均未改变；源数据库文件哈希也未改变。见 [演练报告](../artifacts/evt01-migration.json)。这不表示已迁移原运行数据库；下次实际启动时按项目既有机制应用 schema 迁移，旧行可继续按需读取。

## 验收与下一步

[事件契约测试](../tests/test_event_contract.py) 覆盖 8 种消费类型往返、未知版本的前置拒绝、来源标识分域、嵌套数据隔离、旧格式时间假设、JSON/投影损坏、迁移副本保护和多批次复验。[HTTP 接线测试](../tests/test_runtime_wiring.py) 在 legacy、Minimal、MVSC 装配成功/失败配置下验证实际消费者及持久元数据。

完整回归 **1279 项 Python 测试通过**，静态检查与文档链接检查通过；受保护默认数据文件哈希保持不变。回归记录见 [EVT-01 validation](../artifacts/evt01-validation.json)。固定 Mock 输入的当前运行采样见 [EVT-01 runtime](../artifacts/evt01-runtime.json)。下一项 **STA-01** 为状态投影建立 revision、StateDelta 和提交规则，避免过时结果覆盖新状态；之后 **EVT-02** 再处理持久 inbox、outbox 与未知动作结果对账。

来源 ID 也没有修复 GitHub poller 的分页、内存断点或拒绝后的重试策略。尤其当前轮询断点不以成功接纳为提交条件，这属于后续连接器/EVT-02 的交付保证工作，不应从本项的稳定标识推断上游事件不会遗漏。
