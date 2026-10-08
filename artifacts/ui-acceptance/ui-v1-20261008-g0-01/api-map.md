# UI 与 API 依赖映射
候选 `ui-v1-20261008-g0-01`；32 项客户端接口映射，均已对照实际路由装饰器及前端调用位置。此轮没有请求服务。

## 请求、身份与副作用
| 编号／区域 | 方法／路径 | 操作影响 | 身份与竞争字段 | 结果／错误 | 开关／依赖 |
| --- | --- | --- | --- | --- | --- |
| A01 · CHAT-02 | GET `/api/chat/modes` | 读取能力（可初始化 LLM adapter；不是提交任务） | mode | normal/deep 能力；失败有回退，真实能力待验 | 完整服务 |
| A02 · CHAT-01,CHAT-03,MON-02 | POST `/api/chat` | 写入：接纳事件、执行任务；remember=true 可保留用户陈述 | 新 task_id/event_id；提交时 mode 固定 | accepted true/false；不确认则 unknown，查询而非自动重发 | runtime admission |
| A03 · CHAT-01,MON-02 | POST `/api/chat/stream` | 写入：接纳任务并流式等待；断流不取消任务 | X-EVA-Task-ID 与终态身份 | SSE 终态／断流／unknown；迟到查询不能覆盖新任务 | runtime admission |
| A04 · CHAT-03 | GET `/api/chat/result/{task_id}` | 查询结果；不重复派发任务，registry.lookup 内部持久化行为需联调 | 原 task_id | 202 执行中／200 终态／404 未知或过期／503 存储不可用 | result retention |
| A05 · CHAT-07,SET-06,MVSC-01,MON-01 | WEBSOCKET `/ws` | 接收状态／chat_reply；通道连接与认证，非新任务提交 | task_id；channel；4401/4403 | 连接／重连／迟到回执／登录更新 | 服务认证；不采集实际令牌 |
| A06 · MAP-01,MAP-02 | GET `/api/memory/graph` | 读取受限投影，不修改 DB/vault | q,tiers,limit<=600,edge_limit<=3000 | 200 范围／422 tiers 无效／503 来源不可用 | memory source |
| A07 · MAP-04 | GET `/api/memory/graph/neighbors` | 读取邻居分页；客户端合并记录 | tier,record_id,limit,offset；neighborVersion | 200/404 过期/422/503；旧读取不覆盖新中心 | memory source |
| A08 · MAP-05,MAP-09,MAP-10 | GET `/api/memory/graph/node` | 读取单条记录；不补造缺失记录 | tier,record_id；detailVersion | 200/404 缺失/422/503；保留已显示范围 | memory source |
| A09 · MAP-10 | GET `/api/memory/graph/dangling` | 读取悬空关系诊断，不修复关系 | limit,offset；列表读取代数 | 页／空／503／详情404 | memory source |
| A10 · MAP-12 | GET `/api/obsidian/export` | 生成 ZIP 供下载，不写 vault | limit<=600；button 防重复 | ZIP／503；原生保存待验 | memory source |
| A11 · MAP-01,MAP-06 | GET `/api/goals/graph` | 读取已存目标投影，不检查文件 | limit<=40,offset,q | 200/503 功能未启用或存储失败 | enable_business_goals |
| A12 · MAP-11 | POST `/api/goals` | 写入：登记目标并可能同步已收到回执；不执行任务或自动检查文件 | 已有 task_id,event_id；一任务绑定一个目标 | 201／404 回执缺失／409 绑定冲突／422／503；不自动重发 | business_goals 与 accepting |
| A13 · MAP-11 | GET `/api/goals/by-task/{task_id}` | 状态同步：_reconcile 可 store.record_receipt 落库；不能标完全只读 | boundTask；_reconcile 读取 task 回执 | 200／404 未登记／409／503；查询不会再次 create 或 verify | business_goals |
| A14 · MAP-06 | GET `/api/goals/{goal_id}/history` | 读取历史检查记录，不运行新检查 | goal_id,limit<=50,offset；goalHistoryVersion | 200／404／409／503；错误保留旧记录 | business_goals |
| A15 · MAP-06 | POST `/api/goals/{goal_id}/verify` | 写入：指定文件核验与检查记录；先同步回执 | goal_id,expected_version 严格整数 | 200／404／409 版本冲突／422／503；前端不推断超时结果 | business_goals 与 accepting |
| A16 · MAP-06 | POST `/api/goals/{goal_id}/cancel` | 写入：取消目标跟踪；不取消已接纳聊天任务 | goal_id,expected_version | 200／404／409／422／503 | business_goals |
| A17 · MAP-07 | GET `/api/action-context/{task_id}/graph` | 读取已有输入／意图引用，不核验现内容 | task_id,event_id；actionInputVersion | 200／404／409 来源不一致／422／503 | enable_action_context 依赖 enable_durable_requests |
| A18 · MAP-08 | GET `/api/tool-observations/{action_id}` | 读取已存独立观测，不采样文件 | action_id,event_id | 200／409 binding conflict／422／503 | enable_processing_episodes |
| A19 · MAP-08 | POST `/api/tool-observations/{action_id}` | 写入：服务端采样固定文件并追加观测；不重做原动作 | action_id,event_id,expected_count<32 | 201／409 并发冲突／422／503；超时先读已有记录 | processing_episodes 与 accepting |
| A20 · CHAT-07,SET-06,MAP-02,MON-01,MVSC-01 | GET `/api/runtime` | 读取运行观察，非任务控制 | AbortController；cache no-store | 200／503 controller 缺失／登录更新 | 完整 runtime；预览未连接 |
| A21 · LIST-01,SET-06,MON-01 | GET `/api/memory/tiers` | 读取层统计；format=explorer 用于列表 | format；各层可用性 | 200／来源缺失分层说明；旧客户端失败待联调 | memory |
| A22 · LIST-01 | GET `/api/memory/entries/{tier}` | 读取按层条目 | tier,limit,offset | 200 页／空／错误；显示来源字段 | memory |
| A23 · LIST-02 | POST `/api/memory/search` | 读取：POST 搜索，不按 verb 判写入 | query,tiers,limit | 200 结果／空／验证错误；旧 fetch 认证待验 | memory |
| A24 · SET-06,MON-01 | GET `/api/memory/world` | 读取世界投影 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A25 · SET-06,MON-01 | GET `/api/policy/state` | 读取策略观察 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A26 · SET-06,MON-01 | GET `/health/diagnostic` | 读取健康诊断 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A27 · SET-06,MON-01 | GET `/health/ready` | 读取就绪状态 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A28 · SET-06,MON-01 | GET `/api/state` | 状态同步：更新 system_state 计数，非只读不可变快照 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A29 · SET-06,MON-01 | GET `/api/executors/audit/replay` | 读取审计时间线，不执行动作重放 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A30 · SET-06,MON-01 | GET `/api/agents` | 读取 Agent 清单 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A31 · SET-06,MON-01 | GET `/api/scheduler/jobs` | 读取已有调度项 | 查询参数或周期轮询 | 200／无数据／失败分支待实际服务核验 | 完整 container |
| A32 · MVSC-01 | GET `/api/mvsc/ablation` | 读取功能开关，不调用 toggle POST | 实验运行可用性 | 200／功能关闭或未装配返回分支待验 | MVSC runtime attachment |

## 源码与关联测试
| 编号 | 客户端 | 服务端处理函数 | 关联测试文件（本轮未运行） |
| --- | --- | --- | --- |
| A01 | [ui/web/static/chat.js:30](../../../ui/web/static/chat.js) | [app/api_routes/routes_chat.py:68](../../../app/api_routes/routes_chat.py) · chat_modes | tests/js/test_cube_chat.cjs, tests/test_chat_modes.py |
| A02 | [ui/web/static/chat.js:632](../../../ui/web/static/chat.js) | [app/api_routes/routes_chat.py:78](../../../app/api_routes/routes_chat.py) · chat | tests/js/test_chat_receipts.cjs, tests/test_request_receipts.py |
| A03 | [ui/web/static/chat.js:709](../../../ui/web/static/chat.js) | [app/api_routes/routes_chat.py:120](../../../app/api_routes/routes_chat.py) · chat_stream | tests/js/test_chat_receipts.cjs, tests/test_chat_modes.py |
| A04 | [ui/web/static/chat.js:330](../../../ui/web/static/chat.js) | [app/api_routes/routes_chat.py:261](../../../app/api_routes/routes_chat.py) · get_chat_result | tests/js/test_chat_receipts.cjs, tests/test_request_receipts.py |
| A05 | [ui/web/static/chat.js:43](../../../ui/web/static/chat.js) | [app/main.py:72](../../../app/main.py) · websocket_endpoint | tests/js/test_chat_receipts.cjs, tests/test_cloudflare_access.py |
| A06 | [ui/web/static/memory_graph.js:618](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_memory_graph.py:36](../../../app/api_routes/routes_memory_graph.py) · memory_graph | tests/js/test_memory_graph.cjs, tests/test_memory_graph.py |
| A07 | [ui/web/static/memory_graph.js:588](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_memory_graph.py:76](../../../app/api_routes/routes_memory_graph.py) · graph_neighbors | tests/js/test_memory_graph_interactions.cjs, tests/test_memory_graph.py |
| A08 | [ui/web/static/memory_graph.js:803](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_memory_graph.py:61](../../../app/api_routes/routes_memory_graph.py) · graph_node | tests/js/test_memory_diagnostics.cjs, tests/test_memory_graph.py |
| A09 | [ui/web/static/memory_diagnostics.js:139](../../../ui/web/static/memory_diagnostics.js) | [app/api_routes/routes_memory_graph.py:51](../../../app/api_routes/routes_memory_graph.py) · graph_dangling | tests/js/test_memory_diagnostics.cjs, tests/test_memory_graph.py |
| A10 | [ui/web/static/memory_graph.js:1052](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_memory_graph.py:91](../../../app/api_routes/routes_memory_graph.py) · obsidian_export | tests/test_memory_graph.py |
| A11 | [ui/web/static/memory_graph.js:618](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_goals.py:115](../../../app/api_routes/routes_goals.py) · goal_graph | tests/js/test_memory_graph.cjs, tests/test_business_goal_api.py |
| A12 | [ui/web/static/goal_create.js:70](../../../ui/web/static/goal_create.js) | [app/api_routes/routes_goals.py:86](../../../app/api_routes/routes_goals.py) · create_goal | tests/js/test_goal_create.cjs, tests/test_business_goal_api.py |
| A13 | [ui/web/static/goal_create.js:70](../../../ui/web/static/goal_create.js) | [app/api_routes/routes_goals.py:108](../../../app/api_routes/routes_goals.py) · goal_for_task | tests/js/test_goal_create.cjs, tests/test_business_goal_api.py |
| A14 | [ui/web/static/memory_graph.js:505](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_goals.py:129](../../../app/api_routes/routes_goals.py) · goal_history | tests/js/test_memory_graph.cjs, tests/test_business_goal_api.py |
| A15 | [ui/web/static/memory_graph.js:571](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_goals.py:165](../../../app/api_routes/routes_goals.py) · verify_goal | tests/js/test_memory_graph.cjs, tests/test_business_goal_api.py |
| A16 | [ui/web/static/memory_graph.js:571](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_goals.py:158](../../../app/api_routes/routes_goals.py) · cancel_goal | tests/js/test_memory_graph.cjs, tests/test_business_goal_api.py |
| A17 | [ui/web/static/memory_graph.js:472](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_action_context.py:28](../../../app/api_routes/routes_action_context.py) · get_action_context_graph | tests/js/test_memory_graph.cjs, tests/test_action_context_graph.py |
| A18 | [ui/web/static/memory_graph.js:549](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_tool_observations.py:36](../../../app/api_routes/routes_tool_observations.py) · observation_history | tests/js/test_memory_graph.cjs, tests/test_tool_observation_api.py |
| A19 | [ui/web/static/memory_graph.js:549](../../../ui/web/static/memory_graph.js) | [app/api_routes/routes_tool_observations.py:49](../../../app/api_routes/routes_tool_observations.py) · reconcile_tool | tests/js/test_memory_graph.cjs, tests/test_tool_observation_api.py |
| A20 | [ui/web/static/runtime_status.js:25](../../../ui/web/static/runtime_status.js) | [app/api_routes/routes_runtime.py:11](../../../app/api_routes/routes_runtime.py) · runtime_state | tests/js/test_runtime_status.cjs, tests/test_runtime_activity.py |
| A21 | [ui/web/static/memory_explorer.js:28](../../../ui/web/static/memory_explorer.js) | [app/api_routes/routes_memory.py:55](../../../app/api_routes/routes_memory.py) · memory_tiers | tests/test_memory_graph.py |
| A22 | [ui/web/static/memory_explorer.js:83](../../../ui/web/static/memory_explorer.js) | [app/api_routes/routes_memory_explorer.py:37](../../../app/api_routes/routes_memory_explorer.py) · get_memory_entries | tests/js/test_memory_graph.cjs |
| A23 | [ui/web/static/memory_explorer.js:145](../../../ui/web/static/memory_explorer.js) | [app/api_routes/routes_memory_explorer.py:156](../../../app/api_routes/routes_memory_explorer.py) · search_memory | tests/test_memory_graph.py |
| A24 | [ui/web/static/settings.js:110](../../../ui/web/static/settings.js) | [app/api_routes/routes_memory.py:185](../../../app/api_routes/routes_memory.py) · world_model | tests/js/test_runtime_status.cjs |
| A25 | [ui/web/static/settings.js:117](../../../ui/web/static/settings.js) | [app/api_routes/routes_policy.py:16](../../../app/api_routes/routes_policy.py) · get_policy_state | tests/js/test_runtime_status.cjs |
| A26 | [ui/web/static/settings.js:146](../../../ui/web/static/settings.js) | [app/api_routes/routes_health.py:27](../../../app/api_routes/routes_health.py) · health_diagnostic | tests/js/test_runtime_status.cjs |
| A27 | [ui/web/static/settings.js:153](../../../ui/web/static/settings.js) | [app/api_routes/routes_health.py:22](../../../app/api_routes/routes_health.py) · health_ready | tests/js/test_runtime_status.cjs |
| A28 | [ui/web/static/settings.js:160](../../../ui/web/static/settings.js) | [app/api_routes/routes_chat.py:325](../../../app/api_routes/routes_chat.py) · get_state | tests/js/test_runtime_status.cjs |
| A29 | [ui/web/static/settings.js:173](../../../ui/web/static/settings.js) | [app/api_routes/routes_executors.py:124](../../../app/api_routes/routes_executors.py) · audit_replay | tests/js/test_runtime_status.cjs |
| A30 | [ui/web/static/settings.js:183](../../../ui/web/static/settings.js) | [app/api_routes/routes_agents.py:21](../../../app/api_routes/routes_agents.py) · list_agents | tests/js/test_runtime_status.cjs |
| A31 | [ui/web/static/settings.js:184](../../../ui/web/static/settings.js) | [app/api_routes/routes_scheduler.py:11](../../../app/api_routes/routes_scheduler.py) · scheduler_jobs | tests/js/test_runtime_status.cjs |
| A32 | [ui/web/templates/mvsc_dashboard.html:120](../../../ui/web/templates/mvsc_dashboard.html) | [app/api_routes/routes_mvsc.py:68](../../../app/api_routes/routes_mvsc.py) · get_ablation_config | tests/mvsc/test_e2e.py |

## 非业务接口与变更边界
- 品牌选择、审阅、取消、应用／撤销、主题与语言仅管理本地呈现偏好。品牌导出读取 `/static/brand-source.json` 与批准组件文件，写入浏览器下载，不导入服务参数或凭据。
- 剪贴板复制是显式本地能力；`obsidian://` 为外部应用打开。图谱 GET 导出只生成档案，不主动同步或覆盖笔记库。
- G0-F02：目标查询同步回执；G2 验收约束应为不派发新任务／不新做文件检查，而非假设零持久化写入。目标 cancel 也不等于取消聊天执行。
- API 开关读取源码默认关闭；本轮没有读取实际环境变量或启动状态，不将源码默认值当作当前运行配置。
- 产品调用端：chat＋cube-avatar；brand-system＋brand-config＋brand-package；memory_graph＋goal_create＋memory_diagnostics；settings/app/MVSC＋runtime；memory_explorer。共享呈现变更需覆盖相关调用端。
- 关联后端文件：聊天／admission／结果 registry；memory graph projection／neighbors；business goals／Episode／action context；runtime observation。依赖指纹已在基线中单列，不能由 UI 的 34 项 Python 报告概括。
- [working-tree-status.txt](working-tree-status.txt)记录相关路径的真实修改／未跟踪状态。候选为工作树字节快照，Git HEAD 仅参考；没有提交、暂存、重置或清理文件。
- 完整路由目录是源码索引，不代表所有管理接口都有 UI 入口或纳入本次联调；本表仅列当前客户端调用契约。
