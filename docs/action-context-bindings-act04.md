# ACT-04：实际上下文版本与 Agent 行动意图关联

2026-10-01，在 ACT-02 请求派发与 ACT-03 未领取恢复的基础上，增加默认关闭的
`EVA_ENABLE_ACTION_CONTEXT=true`。它要求 `EVA_ENABLE_DURABLE_REQUESTS=true` 和
SQLite。legacy / Minimal 的普通、同步、SSE 请求复用同一个共享处理器入口；
本轮没有修改线上配置或重启 Cloudflare 服务。

## 实际输入的版本

世界图的 `context_projection()` 在图锁内输出有限投影和 `reference`：
`world_context_projection`、实例 scope、非负 revision、投影内容 SHA-256。
公开顶层字段的变化、实体/关系写入与删除、S4 合并均推进该实例的版本；
组合更新助手方法持有同一图锁，不会读到方法内只更新了一半的字段。
实体、关系及邻居的公开 getter 返回副本，调用者修改返回值不会绕过版本边界。
私人 `_entities` 等内部字段不属于受支持的修改接口。

世界 scope 使用新实例 UUID，重新构造/恢复时更换。版本从本实例初始状态开始，
不是跨重启的耐久世界 revision；同一个数字在不同 scope 下不表示同一状态。
旧全量世界快照仍使用既有格式，Episode `state_before/after` 的未知版本不改写。
上下文投影哈希仅标识该有限视图，不标识未选中的整张图。

启用时，在记忆 SQLite 中安装 `memory_view_revision` 和 S2/S3 的
INSERT / UPDATE / DELETE triggers。版本在原始行写入事务中推进，原始写入
回滚也回滚计数，包括绕过 TieredMemoryManager 的同库 SQL 写入。scope 耐久保存，
重复安装与正常重启保持同一 scope。初始 0 是安装时既有数据的基线，不是旧数据
的历史版本。恢复旧数据库备份、克隆后分叉及跨数据库复制的版本协调不在此协议内。

`recall_snapshot()` 先读取版本以固定 WAL 快照，再从同一连接检索 S2/S3；
读期间的实际并发写入不会混进所读行或版本。读作用域开启 query_only，拒绝
嵌套事务以及会提前提交的 write adapter；退出、异常时恢复连接的正常写入能力。
每次最多返回 10 条，与实际 context 使用同一组行；另外保存每项 tier、memory ID
和该项内容哈希，不保存正文。查询、时间过滤及排序影响选中的集合，因此同一
数据库 revision 的不同检索可以有不同的内容哈希。

S1、Governor 独立表及向量索引不由 S2/S3 版本代表。混合检索的向量排序仍是独立
索引视图，候选行内容与 active 状态从固定 SQLite 快照读取。该实现没有增加
向量索引的事务版本或硬实时检索预算；慢检索可能延长 WAL 读快照的存活时间。

## 执行入口

共享处理器在当前 policy、路由、上下文和任务准备完成后，进入实际 worker 前：

1. 确认服务器登记仍有当前实例的耐久请求领取权、身份匹配且原期限未到。
   缓存过期/被清理、易失登记或同 ID 墓碑不能被当作内部事件而绕过该检查。
2. 对实际 AgentTask 的 kind / payload 和实际 context 分别计算有限 JSON 哈希。
   保留世界/记忆引用及逐项记忆 ID/哈希；上下文、Persona、模型正文和用户文本
   不进入新意图的参数。
3. 在同一 SQLite 事务内保存下一个派发 revision、原来源事件、
   `runtime:agent_invocation` 意图与 executing 领取凭证。
   意图以固定 `agent_invocation` slot 绑定原事件，并引用父请求处理 action ID。
4. 保存成功才调用实际 worker。保存失败返回 `action_context_unavailable`，
   不调用 Agent；不是删除原请求或自动重试的入口。
5. worker API 返回后记录 handler_return；异常记录 handler_exception。返回失败
   或超时只能确认为 unknown，不证明内部工具没有副作用。完成观察保存失败时，
   首个请求回执为 `action_context_outcome_unknown`，实际处理退出时再封存未知。
   HTTP 终态可早于最后的退出封存，不能将响应到达当作全部 worker 已退出。

上下文准备阶段既有的世界/记忆写入早于这个 Agent 意图事务，仍在其外；世界锁和
记忆读事务各自保证自己的视图，没有跨组件共同瞬间或全系统状态/行动事务。
引用表示执行实际使用的历史输入，不要求之后的共享世界仍保持该版本，也不证明
检索内容或推理为真。内部非耐久事件尚未接入该 Agent 意图边界。

领取后的进程中断将 executing 意图封存 unknown，保留原上下文和 intent 哈希；
ACT-03 不重新发布该请求。即使 Agent 已保存 completed 而父请求回执丢失，
父请求仍是结果未知，不由子行动返回推断业务成功，也不自动补做副作用。

## 读取与 Episode

认证 `GET /api/action-context/{task_id}` 返回输入引用、逐项记忆 ID/哈希、父行动、
Agent 意图、派发 revision 和处理器观察。读取不推进请求期限、不补送目标通知、
不重放 Agent 或工具，不返回 claim token 或原上下文。功能未挂载 503；尚未产生
Agent 意图 404；损坏或不一致记录 503。已有记录在正常重启后可继续查询。

同时开启处理 Episode 时，实际 agent_invocation 使用同一个 action ID 与请求哈希，
metadata 保存三项 `agent_intent` 引用：action_id、state_version、request_hash。
嵌套工具可沿原 Episode 父动作引用追到这条实际 Agent 意图；没有为旧 Episode
回填记录或更改旧世界快照版本。`/api/runtime` 报告请求开关和账本实际挂载配置。
本轮增加供后续图谱连接使用的读取契约，粒子页面的跨层输入连接仍待下一阶段接入。

## 验证与后续

[test_action_context.py](../tests/test_action_context.py)：23 项定向测试通过，覆盖
真实 SQLite 行/版本回滚、同库不同连接并发写入、版本 scope 重启、固定读事务及
写入保护、世界 getter 副本、原期限/缓存清理、意图插入失败的整体回滚、
legacy / Minimal 普通与 SSE 实际 worker 入口、Episode ID 关联、认证读取、
观察保存失败的未知结果与去重，以及真实子进程领取/效果/返回观察三个退出窗口。
另外完成架构、恢复、生命周期、工具收据与图谱相关回归。最终质量记录：

- Python 全量 **1952 passed，1 skipped，245.42s**。
- JavaScript 全量 **386 passed，0 failed**；本轮未修改 JavaScript。
- 全仓 Ruff、20 个本轮 Python 文件的 Ruff 格式检查、相关已跟踪改动的
  `git diff --check` 通过。
- 14 个协议/存储/处理器/路由生产文件的定向 mypy 通过，使用
  `--follow-imports=silent --disable-error-code=import-untyped --check-untyped-defs`。
  扩展检查包含 composition 时仍有 6 个既有类型错误，不宣称全项目类型通过。
- 启动预检中依赖、配置、数据目录和数据库完整性通过；8000 端口由现有服务占用。
  未停止现有服务以消除该项提示。Black 未安装，格式检查使用 Ruff。

下一阶段接入粒子图谱中的行动—输入记忆连接和历史引用详情，再推进世界/记忆的
耐久状态提交协议与跨组件观察/补偿。部署切换、真实外部服务、浏览器视觉和系统
断电验收仍需取得各自证据。本项不等于全项目动态认知架构已完成。

2026-10-08 后续：[ACT-05](action-input-graph-act05.md) 已实现由目标/回执加载实际 Agent 行动及历史输入引用的图谱接口与粒子显示。引用不还原历史正文或核验当前记忆；仍未启用生产可选开关。
