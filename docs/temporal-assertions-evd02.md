# EVD-02：时间化断言与生命周期

EVD-02 把“某个来源在某个时间观察到一个主张”与“它现在仍然成立”分开。
来源标签仍然不是事实证明；时间窗口和生命周期只约束系统如何使用这条主张。

## 断言结构

`TemporalAssertion` 保存：

- `subject`、`predicate`、`value`
- `observed_at`：观察发生时间
- `valid_from` / `valid_until`：主张适用的时间窗口，结束时间为排他边界
- `provenance`、`source_event_id`、`evidence_ids`
- `status`：active、retracted、superseded、expired、disputed 或 unknown

观察时间不等于有效时间。旧观察可以保留用于审计，但在有效窗口外不会被
`is_current()` 或 `current_assertions()` 返回为当前断言。

## 撤销与替代

`retract()` 和 `supersede()` 返回带生命周期记录的新副本，不删除原断言。
撤销需要事件 ID 和理由；替代会记录 `superseded_by`。因此恢复、审计和冲突
分析仍能追溯原始主张。当前筛选器不会自动合并冲突值，也不会因为来源重复
而把断言升级为 `verified_fact`。

## 版本与未知数据

当前结构版本为 1。读取未知版本会降级为 `unknown`，不能继续作为当前断言。
非法时间范围、非 JSON 值和缺少必要字段会拒绝读取。该模块目前是可复用的
断言契约；将实体/记忆存储迁移到断言历史表，以及 API/UI 展示撤销链，属于
后续接入工作。
