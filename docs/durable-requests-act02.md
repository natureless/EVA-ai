# ACT-02：实际 HTTP 请求的耐久接纳与领取

2026-10-01，本轮代码与隔离验收。默认关闭的 `EVA_ENABLE_DURABLE_REQUESTS`
将请求接纳、领取和完整终态回执接到实际 legacy/Minimal 共享的
`ResultRegistry` / `EventProcessor`。它不替换消费者，也不启动 MVSC 实验循环。

后续 [ACT-03](unclaimed-request-recovery-act03.md) 增加独立、默认关闭的未领取
请求续跑开关。以下无重放/未执行恢复规则仍适用于未开启该开关的 ACT-02 路径；
已领取未知请求的禁止自动重试规则不变。

## 实际行为

三个聊天入口 `/api/chat`、`/api/chat/sync`、`/api/chat/stream` 在入队前保存
服务器生成的 task ID、完整源事件信封、源内容哈希、UTC 截止时间和保留期。
写入失败返回 503，不发布事件；同一来源事件再次提交返回 409 和原 task/event ID，
客户端可以查询原回执。同一 ID 改变内容也不会再次执行。

共享处理器在模型、记忆和工具调用前，统一检查已登记的完整事件内容。在一个
SQLite 事务中提交请求派发状态 revision、源事件、`runtime:process_event` 意图、
随机领取凭证及请求 claimed 状态。任一写入失败全部回滚，不进入处理主体。
处理主体仍执行原有授权、审查和工具边界，意图存在不授予额外权限。

派发状态保存在 `cognitive_*` 表的独立 `{mvsc_subject_id}:requests` 主体中。
这是请求领取账本，不能作为真实 World Model 或整个 EVA 的状态 revision。
领取后没有租约、自动重试或抢占；请求的审查回复和业务目标完成仍是不同证据。

`request_dispatch_receipts` 为 schema v1：task/event 唯一、源身份、UTC 时间、
pending/claimed/terminal 状态、行动引用、首个完整回执及可重送的摘要通知。
行与信封、意图、历史状态、时间和投影字段都在读回时校验。容量/过期扫描使用
专用列和索引，不逐条解析完整回复 JSON。单个源事件上限 64 KiB，完整回执
上限 256 KiB，整行上限 512 KiB；容量复用 `EVA_RESULT_REGISTRY_CAPACITY`。
恢复和通知每批最多 256 项，启动时重复处理到结束，不静默丢弃超出一批的项。

## 终态、恢复和目标关联

| 中断窗口 | 启动期结果 | 自动执行 |
| --- | --- | --- |
| 已登记、尚未领取 | 未过原期限为 rejected；已过原期限为 expired，均明确未执行 | 无 |
| 已领取、尚无可验证终态 | outcome_unknown，不推断是否产生副作用 | 无 |
| 完整回执已保存 | 原始回复、review、mode 等在原保留窗口内可再次查询 | 无 |
| 仅 Episode/目标保存了有效终态摘要 | 保留真实处理终态；`reply_available=false`、正文为空、review 未评估 | 无 |

后两条不要求旧内存登记项存在。`GET /api/chat/result/{task_id}` 在重启后
读取校验通过的持久回执，损坏或不可读返回 503，过期/缺失返回 404。
不会延长原终态保留期；完整正文过期后有界清理，紧凑身份/终态墓碑保留，
防止旧来源 ID 被再次当作新任务。已有行动历史里的源事件另行保留，未实现
统一历史归档或数据擦除策略。内容保存在配置的私有 EVA 数据库中。

首个内存终态和首个耐久终态均不可被迟到结果覆盖。回执写入/通知失败不会
改写已给出的回复；处理器退出时只重试回执持久化，并记录真实 handler 返回。
如果所有终态存储都失败，重启后按已领取未知处理，不能宣称无损交付。

启动顺序先用已保存的请求/目标摘要恢复 Episode，再恢复请求终态、重送目标
摘要，并补齐“通知已确认，但目标随后插入”的缺口，最后执行已有目标恢复规则。
通知提交后确认失败只会重送相同摘要，不重做行动。跨存储不持有嵌套写事务。
既有目标规则仍把未完成的验证恢复为 interrupted；处理 succeeded 不变成
业务 completed，也不会自动进行文件核验。

活动 handler 阻止账本恢复与关闭。RuntimeController 仍在实际处理器/agent worker
退出后关闭连接，短停机超时保留依赖，释放工作后可重试关闭。进程内登记项
保留期结束也不会丢失实际 handler 生命周期计数。

## 配置与边界

仅支持 SQLite 和单一活动运行实例。默认 `false`；启用需要正常重启部署。
本轮未修改线上环境、重启 Cloudflare 服务或切换生产消费者。
`/api/runtime` 展示实际挂载、pending/claimed/terminal、活动 handler、通知积压
与错误；`ResultRegistry.stats()` 另记录持久化错误。默认路径仍为内存登记。
直接内部 `create(..., event=None)`、非聊天来源和无 correlation 的事件没有
自动升级为耐久 HTTP 请求，避免隐式改变其他接纳协议。

此实现不包含：重启后自动续跑未领取请求、统一 World/Memory 事务、所有嵌套
工具的独立派发、远程幂等/对账、通用补偿、跨运行实例协调和浏览器视觉验收。
下一开发入口是可明确证明未领取请求的恢复调度、原期限/授权复查及 Minimal
快照去重协调；已领取未知结果继续通过独立观察与人工决策处理，不解除重试限制。

## 验证

组件测试见 [test_request_dispatch_store.py](../tests/mvsc/test_request_dispatch_store.py)：
实际 SQLite 跨连接竞争、四张表写失败原子回滚、原期限/保留期、正文大小、
损坏信封与内容、事件替换拒绝、活动关闭保护、回执失败后只补写记录、通知恢复。
独立子进程以 `os._exit` 验证登记后、领取后、文件副作用后、回执提交后四个窗口。

实际 HTTP 测试见 [test_durable_request_api.py](../tests/test_durable_request_api.py)：
legacy/Minimal × 三聊天入口、真实领取先于处理、重启后回复/审查/模式/目标/Episode
关联、来源冲突、接纳/领取失败不产生处理记录、损坏回执 503、仅摘要恢复明确正文
不可用、目标登记窗口及活动 worker 停机重试。Mock LLM 的 review 保留原始状态，
没有把未评估结果伪造为审查通过。

2026-10-01 Windows 全量 Python **1897 passed, 1 skipped in 194.26s**，
全量 JavaScript **386 passed**。全项目 Ruff、15 个本轮 Python 文件格式检查、
11 个生产文件 mypy（`--follow-imports=silent --disable-error-code=import-untyped
--check-untyped-defs`）通过；本轮文件 `git diff --check` 通过。
启动预检仅 8000 端口占用未通过，其余依赖/配置/数据库完整性检查正常。
前端明确展示“仅恢复回执、原始正文未保存”，行为测试验证不会自动重新提交。
没有新增真实浏览器、远程副作用、断电或生产切换验收。
