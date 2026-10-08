# RST-02：恢复副本、冲突诊断与有限世界状态读取

状态：2026-09-12 已实现。对应 [v3 开发计划](source-review-and-roadmap-v3.md) 的 RST-02，沿用 [RST-01 合并规则](world-restore-rst01.md)。

## 交付内容

1. [恢复工具](../world/recovery_tools.py) 与 [CLI](../scripts/world_recovery.py)：SQLite 一致性备份、原始快照备份、冲突诊断、规范化候选、校验、候选/原样恢复副本。
2. [WorldModelGraph](../world/world_model.py) 的 `counts()` 与 `context_projection()`，以及 [Minimal Brain 接入](../packages/minimal_brain/integration.py)。
3. [可重复的读取基准](../scripts/benchmark_world_projection.py)、恢复与有限查询测试。
4. 原有启动测试的临时目录隔离修复，以及本轮测试数据污染的修复记录。

## 恢复工具的契约

`prepare` 只写入一个新目录，拒绝已有输出目录。原始快照逐字节保存，数据库通过 SQLite backup API 复制，包括已经提交但尚在 WAL 中的内容；不使用普通文件复制来备份活跃数据库。

备份包包含：

| 文件 | 用途 |
| --- | --- |
| `original.snapshot.json` | 原始快照的完整字节，保留被规范化候选舍弃的历史冲突值 |
| `database.sqlite` | SQLite 一致性备份，验证 integrity_check 的实际结果 |
| `candidate.snapshot.json` | 按 RST-01 合并规则生成的候选；其他快照部分保留 |
| `report.json` | 实体/边的重复、内容冲突、S4 重叠、保留标识数量与最多 100 个冲突样本 |
| `manifest.json` | 最后生成的完成标记、各文件 SHA-256 和大小 |

诊断中的标识和内容样本使用哈希，CLI 只输出计数；原始数据仍在备份文件中。因此真实备份放在已忽略的 `data/world-recovery/`，不进入版本库。

`verify` 校验文件、数据库完整性，并从原始备份重新生成候选和报告逐项比较。仅修改候选并同步修改其哈希也无法通过重建一致性检查。哈希用于发现损坏和变化，不提供针对攻击者的签名认证。

`restore` 默认导出原样副本，也可选择规范化候选；输出目录必须新建，包含 `eva.db`、`latest.json` 和校验回执。它不覆盖配置中的运行文件。候选副本可用于升级演练，原样副本可用于回滚演练。

数据库备份与快照不是跨存储事务。工具会检查快照在备份窗口内是否变化；发现变化拒绝完成该包。跨进程写入时仍不能宣称取得同一时刻的所有状态。正式切换数据目录前应停止写入，并确认运行实例使用哪份数据；本批没有自动发布候选副本。

备份超时或解析失败时保留未完成目录供检查，没有完整 manifest 的副本不能恢复。下一次执行使用新的输出目录，不覆盖失败现场。

## 使用方式

从项目根目录执行，输出路径必须尚不存在：

```powershell
python -m scripts.world_recovery prepare --snapshot data/snapshots/latest.json --db data/eva.db --output data/world-recovery/run01
python -m scripts.world_recovery verify --bundle data/world-recovery/run01
python -m scripts.world_recovery restore --bundle data/world-recovery/run01 --output data/world-recovery/run01-candidate --variant candidate
python -m scripts.world_recovery restore --bundle data/world-recovery/run01 --output data/world-recovery/run01-rollback --variant original
```

本轮实际备份位于 `data/world-recovery/rst02-20260912/`，两个恢复目录分别带 `-candidate`、`-rollback` 后缀。摘要见 [恢复验证记录](../artifacts/rst02-recovery-validation.json)。

**本轮输入已经是 183 个实体、834 条不同边，未发现内容冲突。** 先前 102,013 条边是历史采样，不是本轮备份工具的输入；不能声称本轮工具把真实数据从 102,013 条修到了 834 条。重复和冲突分支使用隔离测试数据验证。

## 有限读取接口

以下契约和性能数值记录 RST-02 交付状态。后续 [EVD-01](world-provenance-evd01.md) 已增加字段来源与有限最近实体记录，扩展上下文调用方；恢复包写出升级为 v2，来源参与冲突指纹，同时兼容旧 v1 包校验。EVD-01 单独记录扩展后的读取成本。

`counts()` 在图锁下读取两个容器长度，不序列化内容。`context_projection(task_limit=10, entity_limit=10)` 返回有限的 focus、mode、last_loop_id、活动任务、最近实体名称与计数。

任务仅允许 id、name、status、priority、deadline，字符串有长度上限，不复制任意嵌套 properties。两个数量参数必须是 0–100 的整数。最近实体通过 top-K 选择，扫描复杂度为 O(N log K)、辅助空间 O(K)；因此限制的是输出和分配量，不是承诺对任意实体数量都恒定耗时。

该接口不遍历关系边。Minimal Brain 的模型投影使用它，完整 `to_dict()` 保留给快照等需要全量数据的场景。没有改动其他调用方的完整世界模型契约，也没有将未实现的来源语义标为已完成。

## 读取成本实测

Windows / Python 3.11.9；每组 30 次，图构建不计入。时间采用未开启 tracemalloc 的测量，峰值分配单独测量。比较的是同一规范化图上的“完整快照后裁剪”和新接口；新接口还缩小了任务属性集合。

| 数据集 | 旧读取中位数 | 新读取中位数 | 旧峰值分配 | 新峰值分配 |
| --- | ---: | ---: | ---: | ---: |
| 实际快照：183 实体 / 834 边 | 1.258 ms | 0.0245 ms | 281,432 B | 4,240 B |
| 合成规模：1,000 实体 / 100,000 不同边 | 28.940 ms | 0.0683 ms | 19,771,968 B | 4,012 B |

原始测量：[实际快照](../artifacts/rst02-projection-current.json)、[合成规模](../artifacts/rst02-projection-scale.json)。这些是单机内存读取数据，不表示整体聊天延迟获得相同提升，也不包含数据库、LLM 或并发负载成本。

```powershell
python -m scripts.benchmark_world_projection --entities 1000 --edges 100000 --iterations 30 --output artifacts/projection-scale-new.json
python -m scripts.benchmark_world_projection --snapshot data/world-recovery/run01/original.snapshot.json --iterations 30 --output artifacts/projection-snapshot-new.json
```

## 测试隔离问题与修复

检查本轮真实快照变化时，发现 `tests/test_integration_recovery.py` 的三个启动/关闭测试在收集阶段绑定了默认 `bootstrap_system`，未注入临时数据路径。此前全量测试会实际恢复并保存工作区的数据库/快照。这也更正了 RST-01 交付时“历史文件未写回”的说法：当时只读检查本身没有写回，但完整测试集存在上述副作用。

现已移除该静态绑定，为三项测试注入临时数据库、快照、profile、self-model、日志及向量路径，关闭外部连接器和实验模式，并在失败时清理已启动容器。

修复后重新执行全量测试：**1,182 passed in 122.09s**。对实际 `data/eva.db`、`data/snapshots/latest.json`、`data/profile.json`、`data/self_model.json` 记录测试前后 SHA-256，四个文件均不变，见 [隔离验证](../artifacts/rst02-test-data-isolation.json)。本批新增 19 个恢复工具/有限查询测试，Ruff 与启动预检通过。

本轮第一次未隔离全量测试前已创建备份。对比发现仅 `working_memory` 表内容和快照 `memory` 部分变化：工作记忆从 48 条变为 501 条。修复前再次备份受影响状态，确认没有服务监听、数据未出现额外变动后，在事务内将工作记忆恢复为本轮测试前的 48 条，并恢复快照原始字节；其他表内容未改动。详见 [数据修复回执](../artifacts/rst02-test-data-repair.json)。受影响状态副本在 `data/world-recovery/rst02-20260912-after-unisolated-tests/`。

恢复范围只覆盖本轮已有备份能够证明的变化。更早轮次缺少对应运行前备份，不能依据本轮备份声称已完整还原那些变化。

## 后续边界

RST-02 的工具、候选迁移/回滚演练、有限读取与成本测量已完成。运行数据没有被候选迁移包自动替换；本轮对原目录的写入仅用于修复上述已定位的测试副作用。后续 EVD-01 已处理 S4 来源断链，不能用去重、时间新旧或回执成功替代证据可信度。
