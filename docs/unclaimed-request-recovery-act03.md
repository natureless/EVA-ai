# ACT-03：未领取请求的恢复调度

2026-10-01，在 [ACT-02](durable-requests-act02.md) 的实际 HTTP 接纳/领取边界上，
增加默认关闭的 `EVA_RESUME_DURABLE_REQUESTS=true`。它要求同时启用
`EVA_ENABLE_DURABLE_REQUESTS=true`；只有单一活动 SQLite 运行实例可以恢复，
上一实例和 worker 必须已退出。本轮没有修改线上配置或重启 Cloudflare 服务。

## 续跑的证据

启动时逐页检查耐久登记。仅保留 pending、没有 action/start 引用、没有同源
`request_processing` 意图、完整源信封/哈希匹配且原 UTC 截止时间尚未到达的请求。
限定为现有 user_message 来源和已支持的聊天文本、mode、remember/stream 字段。
与已保存终态摘要或行动意图冲突的 pending 记录不能续跑。过期项生成 expired；
claimed 仍按 ACT-02 恢复为结果未知或已验证终态，绝不进入发布队列。

候选 task ID 放入连接私有的 SQLite TEMP 表，来源仍在耐久请求表中。TEMP 表只
是启动候选投影，不充当新的耐久权威；崩溃后可重新从未领取证据构建。启动后的
实时 HTTP 接纳不会自动进入候选集，避免入队拒绝与后台发布竞争导致意外执行。

未开启该开关时保留 ACT-02 的保守行为：启动将未领取请求封存为未执行，不续跑。

## 发布、领取与期限

`RequestRecoveryPublisher` 在消费者启动且启动诊断通过后运行，每次最多读取
16 项候选，按候选游标轮转。只向当前 EventBus 发布；它不调用模型、Agent 或工具。
只有运行时实际 accepting 才发布。队列/登记缓存满时保留候选，之后再次尝试。
队列确认失败时保留已提交 ID，并只重试确认，不在本进程再发布第二份。

登记缓存从耐久记录恢复原 task/event ID、原等待年龄、剩余期限和原终态保留期。
不会按新实例的超时配置重新开始计时；查询尚未进入缓存的请求也能显示 pending，
原期限到达则持久记录 expired。真正的执行入口仍在 `EventProcessor.begin`：
重新校验整个源事件、原 UTC 截止时间，再原子保存派发 revision/意图/领取凭证。
现有处理主体重新进行当前 EVA policy、工具权限和审查检查，不复用旧授权令牌。
这是单用户运行时的服务器接纳证据，不是新增多租户/委托权限恢复协议。

队列发布与 SQLite 领取不构成一个事务。发布后但领取前再次崩溃，可以再次发布
原未领取请求；实际 effect 仍由只领取一次的耐久边界保护。领取后的任何中断均
不自动重试。恢复发布器不提供外部 exactly-once、补偿或远程状态核验。

内部 `create(..., event=None)` 仍是易失登记，但无法用已有耐久 task ID 绕过
领取检查，紧凑墓碑也适用。易失/耐久登记或事件身份冲突会阻止恢复发布。
终态正文过期不解除既有执行身份与重复领取限制。

## Minimal 与目标

Minimal 在启动前核对快照中每个已有事件的耐久未领取证据。只对精确匹配且
内部 goal 为 interrupted 的条目准备续跑；接到原事件时把 goal 版本增加一并
转回 queued。事件 ID、task ID、来源、类型、摘要及完整源内容必须匹配，
completed/failed/cancelled 等快照状态与未领取证据冲突则拒绝装配。
没有清空整份去重记录，也没有给一般终态增加自动重试转换。

尚未入队的恢复条目受到历史裁剪保护。原事件实际接纳后继续走现有注意力、
多时间尺度状态节点和单个慢处理 worker；协调器自己的时钟重新建立，外部请求
仍受原耐久期限约束，不把旧快照动作直接恢复成活跃 future。

业务目标只有 active 且对应请求仍有精确未领取证据时才保留 active，并按当前
目标期限检查到期。已领取、已有处理回执或待核验目标仍使用原恢复规则。
恢复后的处理成功可以转为 pending_verification；它不是业务 completed，
也不会自动运行文件验证器。

## 生命周期与观察

发布器属于 RuntimeController 管理的 producer。停机先关闭 ingress/执行入口，
再等待发布器退出，之后才关闭消费者和存储依赖。发布器的线程或显式 step 仍
在运行时 stop 返回 false，资源保留，释放工作后可以重试停机。

`/api/runtime` 显示恢复配置、实际挂载、运行状态、发布数、背压与错误；耐久表
显示剩余候选数。单项错误不会调用处理器或把未知结果改写为成功。回执通知继续
按原摘要 outbox 交付；没有丢弃尚未得到确认的请求来满足新的缓存容量。

## 验证

[test_request_recovery.py](../tests/test_request_recovery.py) 包含真实 HTTP 接纳后的
`os._exit` 子进程窗口：领取前、领取后、文件副作用后、完整回执后。
实际 legacy/Minimal 启动—恢复—停机验证同一 task/event、deep 模式、原 remember、
单条处理 trace、业务目标关联和旧 Minimal queued 快照的精确去重协调。

还覆盖原期限过期且新实例超时更长、两个连续重启、当前 policy 拒绝、超过一批
候选、队列背压、确认失败只补确认、缓存外期限查询、真实发布线程阻塞时保留存储、
已完成快照冲突、源事件替换、孤立意图冲突，以及易失登记/墓碑不能绕过领取。
测试使用隔离 SQLite、Mock LLM 和临时文件；没有生产吞吐量、远程副作用、
系统断电或真实浏览器的新验收证据。

全量回归发现并修复了审计记录同时间戳下的排序歧义：查询、加载最后一个
chain hash、验证链统一使用 SQLite rowid 打破时间戳相同的并列顺序。新增
[test_audit_ordering.py](../tests/test_audit_ordering.py) 固定时间戳覆盖四种查询
过滤组合、重新创建 logger 后续写和整链验证；相关 67 项测试通过。

最终质量记录：

- Python 全量：`python -m pytest -q --tb=short --show-capture=no`，
  **1929 passed，1 skipped，233.48s**。
- JavaScript 全量：`node --test --test-reporter=dot tests/js/*.cjs`，
  **386 passed，0 failed**。
- `python -m ruff check .` 全仓通过；本轮 15 个恢复接线/新增测试文件
  `ruff format --check` 通过；受跟踪的相关改动 `git diff --check` 通过。
- 13 个恢复相关生产文件的定向 mypy 通过（`--follow-imports=silent
  --disable-error-code=import-untyped --check-untyped-defs`）。额外加入
  `core/executor.py` 检查时，仍有 6 个既有 Windows `resource.setrlimit` /
  `RLIMIT_*` 类型错误，不宣称全项目类型检查通过。
- Black 未安装，未取得 Black 格式检查结果；上述格式检查使用 Ruff。
- `python scripts/preflight.py`：Python、依赖、配置、数据目录和数据库完整性
  通过；唯一未通过项为现有服务占用 8000 端口。本轮未为预检停止该服务。

下一入口仍包括统一世界/记忆 revision 与行动意图关联、未知结果的独立观察与
补偿决策、直接 Agent I/O 的统一工具入口和生产切换演练；本项不能算作整个
EVA 动态认知架构、分布式运行时或 Cloudflare 上线完成。
