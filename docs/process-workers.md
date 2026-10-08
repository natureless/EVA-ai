# Agent 进程后端（WRK-02）

本后端提供可终止、可替换的文本 Agent 子进程。它不是操作系统安全沙箱，
也不使 EVA Core 支持多实例部署。默认仍为具有完整工具能力的线程后端。

## 启用与回退

在环境变量或自己的 `.env` 中设置，然后重启 EVA：

```dotenv
EVA_AGENT_WORKER_BACKEND=process
EVA_AGENT_WORKER_COUNT=2
EVA_AGENT_EXECUTION_TIMEOUT_SEC=120
EVA_AGENT_WORKER_QUEUE_CAPACITY=16
EVA_AGENT_WORKER_QUEUE_TIMEOUT_SEC=5
EVA_AGENT_WORKER_MAX_TASKS=100
```

改回 `EVA_AGENT_WORKER_BACKEND=thread` 并重启即可回退，不需要数据库迁移。
仍然只能运行一个 Uvicorn/Core 进程。进程池在第一项任务时创建，冷启动会增加
该任务的响应时间；应用的 `request_timeout_sec` 只决定同步 API 等待多久，
不等于 Agent 执行期限，尚未完成的任务可通过结果接口继续查询。

| 能力 | thread（默认） | process（本轮） |
| --- | --- | --- |
| 对话和文档文本摘要 | 支持 | 支持内置 chat/docs Agent |
| 父进程已构建的记忆、人格上下文 | 支持 | 以 JSON 数据传入 |
| 搜索/代码文件 Agent | 支持，受现有执行器检查 | 明确返回 policy_denied |
| ChatAgent 文件/记忆/网络/代码工具 | 按原配置 | 不注册工具；不会自动退回线程 |
| 动态注册的自定义 Agent | 支持 | 不支持，返回 unknown_agent |
| 超时终止正在执行的 Agent | 只能报告超时 | 终止子进程，并替换后再接收新任务 |
| CPU、内存上限和后代进程约束 | 无完整隔离 | 尚未实现（WRK-03） |

文档 Agent 只处理传入文本，不负责读取文件。进程内没有工具调用能力，
即使请求附带 capability grants，也不会因此开放文件、网络工具或写入权限。
配置了真实 LLM 时，文本 Agent 仍会调用该供应商 API；“无网络工具”不等于无网络访问。

## 执行与故障语义

`multiprocessing.get_context("spawn")` 为每个槽位创建常驻子进程。
任务和结果使用 Pipe 的 `send_bytes`/`recv_bytes`，负载是严格 JSON，
每条消息不超过 1 MiB、嵌套不超过 64 层。不使用 pickle 传输任务、
数据库连接、执行器或回调；spawn 自身仍使用 Python 的进程启动机制。
人格等已知模型在上下文构建处显式转换为 JSON，任意对象、循环引用、
非字符串键及 NaN/Infinity 被拒绝。

每个子进程至多执行一个任务；父进程最多接纳“Worker 数 + 排队容量”项请求。
输入队列每个槽位 1 条，输出队列 4 条，心跳单独更新状态而不堆积。
Reader/Writer 线程负责 IPC，调用方的期限不依赖阻塞管道写入完成。
正常任务及 idle 时均发送心跳；执行期间超过 10 秒没有心跳会回收子进程。

| 错误 | 含义与处理 |
| --- | --- |
| `queue_full` | 接纳容量已满，立即拒绝，不执行任务 |
| `queue_timeout` | 池启动后等待空闲槽位超时；不终止占用槽位的其他任务 |
| `execution_timeout` | 分派后的执行期限到达，终止并替换 Worker |
| `heartbeat_timeout` | 执行中未收到心跳，终止并替换 Worker |
| `worker_crash` | 执行中进程退出，当前任务失败，替换 Worker |
| `invalid_request` / `invalid_response` | 协议或身份不匹配；无效响应的 Worker 被替换 |
| `worker_capability_unavailable` | 此后端不支持所需文件 Agent，不执行文件操作 |
| `cancelled` | 调用方取消或关闭；正在执行的 Worker 不再复用 |

执行期限采用后端超时和请求 `deadline_sec` 的较小值。它不包含首次池启动、
排队或故障后的替换启动时间。启动有独立的 15 秒/Worker 期限。
启动失败不会降级为线程。任务数达到上限时也会替换 Worker。
空闲进程意外退出后，在下次需要该槽位时替换。
已分派任务不自动重试，以免重复调用外部服务。

关闭先停止接纳新任务，默认给运行任务 2 秒完成，随后终止剩余子进程并回收
IPC 线程。进程终止不能撤销供应商已经接收的请求。

## 审查、追踪与边界

父进程通过共享的 Orchestrator 路径执行令牌/边界检查和审计，再调用远端任务。
输出回到 Core 后仍经过上一轮加入的 `review_result`，审查失败写入错误审计。
SSE/WS 只发送审查后的完整文本，不提前转发模型 token。这不是逐 token 实时输出，
也不是对事实正确性的证明。

Core 在 `AgentTask.trace_context` 中设置任务、循环、关联事件 ID，独立于模型负载。
请求/响应保留这些字段并检查是否匹配，执行器审计使用同一个任务 ID。

`GET /api/config` 的 `runtime.agent_worker` 提供后端类型、进程 PID、存活/忙碌状态、
心跳年龄、排队超时、执行超时、完成数和重启计数，同时明确 `os_sandbox=false`。

`submit()` 用于现有 Core 实体提取等辅助工作，仍在有界线程队列中运行；
任意 Python callable 不能自动变成隔离任务。辅助工作不会占用 Agent 进程槽位，
正在运行的辅助线程也不能强制终止。

Worker 继承宿主环境和 OS 权限，仍可访问宿主文件系统；这里的“未开放工具”
是应用层能力限制，不能抵御任意恶意 Python 代码。Windows Job Object、
CPU/内存硬限制、后代进程管理、环境密钥缩减，以及带授权的工具代理仍待开发。

## 验证

```powershell
python -m pytest -q tests/test_process_worker.py tests/test_process_worker_resilience.py tests/test_worker_protocol.py tests/test_agent_worker.py
```

故障测试使用独立测试入口在真实 spawn 子进程中制造挂起、崩溃、非法帧和错误身份；
生产入口不包含测试命令。测试断言旧 PID 退出、替换进程能继续执行、取消和关闭后
无存活 Worker，另包含并发任务隔离、父进程权限拒绝、审查后 SSE、启动配置与 API 联调。
真实供应商、Linux/容器和 CPU/内存硬隔离未在本轮验证。
