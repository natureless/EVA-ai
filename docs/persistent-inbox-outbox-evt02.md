# EVT-02：持久化 inbox/outbox

EVT-02 为事件处理增加跨重启的接纳和投递边界。它使用独立 SQLite 表，
不替换现有低延迟 EventBus，也不把“持久化”误称为恰好一次执行。

## Inbox

- `enqueue_inbox()` 以 `event_id` 去重，保存完整 v1 `EventEnvelope`。
- `claim_inbox()` 在一个 SQLite 事务内取得 pending 或已过期 lease 的事件，
  并递增 attempts。
- `complete_inbox()` 可重复调用；已完成的事件不会重新执行。
- `fail_inbox()` 支持 terminal failed 或指定 `retry_at` 的重试。

## Outbox

- `enqueue_outbox()` 对 `(event_id, topic)` 建立唯一约束，避免重复外发。
- `claim_outbox()` 使用同样的 lease 恢复规则。
- `mark_outbox_sent()` 和 `fail_outbox()` 提供幂等确认与重试。
- payload 在入库前必须是有限 JSON 值。

## 连接方式与边界

`EventBusAdapter` 新增可选 `durable_queue`。启用时，事件先持久化进 inbox，
再进入现有内存总线；重复 `event_id` 不会再次进入执行队列。恢复 worker
应从 `claim_inbox()` 读取未完成事件，并在成功处理后确认；生产副作用仍需
使用事件 ID 或业务幂等键。当前生产配置未强制启用该可选队列，因此本交付
不会重启或改变已运行的 Cloudflare 服务。

验收测试覆盖：跨实例/跨重启读取、重复接纳、过期 lease 回收、失败重试、
terminal failure、outbox 去重和适配器接入。它验证的是持久化工程契约，
不等于外部工具已经具备恰好一次语义。

[ACT-01](state-action-transactions-act01.md) 增加独立的状态/行动 outbox 事务。
该队列只派发从未领取的意图；已领取后中断的动作封存未知，不复用这里的
租约超时重试规则。两种队列不能混称为外部动作恰好一次保证。实际消费者
接线和生产恢复仍需后续完成。
