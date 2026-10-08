# STA-01：状态 revision 与 StateDelta

STA-01 把“状态更新不能被旧结果覆盖”落实为可执行契约。

## 规则

1. `ConsciousState.version` 是单调递增的 revision。一次成功提交只能把
   `n` 变成 `n + 1`。
2. `StateDelta.base_version` 必须等于提交瞬间的权威 revision。版本不匹配
   时抛出 `StateConflictError`，提交不得产生部分写入。
3. `StateDelta` 只更新 `ConsciousState` 的顶层业务字段。`subject_id`、
   `version`、`tick` 和 `integrity_hash` 是保留字段，由提交规则统一维护。
4. 新状态会重新计算 `integrity_hash`。完整状态提交若哈希不匹配会被拒绝。
5. 读取返回深拷贝，调用方只能通过提交边界改变权威状态。

## 当前实现

`packages/contracts/state.py` 提供 `StateDelta` 与 `StateConflictError`；
`packages/kernel/state_repository.py` 提供 `InMemoryStateRepository`，它是
当前单进程运行时和测试使用的可执行参考实现。后续已实现
`SQLiteStateRepository`，保留 `load`、`commit`、`commit_delta` 和 `replay`
语义，并增加状态/事件/行动意图的原子提交与分页历史，详见
[ACT-01](state-action-transactions-act01.md)。默认 bootstrap 与 HTTP 消费者
尚未接入该耐久仓库；新接口仅在显式依赖注入的实验循环中使用。

## 已验证性质

- 正常增量会产生新 revision，且不改变旧对象。
- 旧 revision、未知字段和保留字段会在提交前拒绝。
- 两个并发的同基线增量只有一个能成功；另一个得到冲突。
- 事件回放按提交 revision 返回，读取副本不会反向修改存储。

这项交付验证的是工程状态契约，不代表认知任务效果或长期并发性能已经测量。
