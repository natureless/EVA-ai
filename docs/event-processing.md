# 事件执行与调度契约

`EventProcessor` 负责执行一个已接收的事件；`CognitionLoop` 负责 FIFO 消费线程。新的调度器直接注入执行服务，无需持有一个未启动的旧循环。

| 组件 | 职责 | 生命周期边界 |
| --- | --- | --- |
| `core.event_processor.EventProcessor` | 入口与行动策略、计划和路由、代理执行、回复审查结果交付、记忆来源标记、世界状态与 trace 更新 | 不创建消费者，不调用 `consume` 或 `task_done` |
| `core.cognition_loop.CognitionLoop` | 从同一个 EventBus 消费、调用服务、确认队列任务、管理消费者线程 | `start`、`request_stop`、`stop(timeout)`、`is_running` |
| `runtime.controller.RuntimeController` | 选择一个消费者，关闭入口，停止生产者和执行，再按依赖顺序回收资源 | 应用启动与关闭的统一所有者 |

执行服务仍注入 EventBus，用于发布维护产生的提醒及更新队列观测。调度器和服务必须使用同一个 EventBus。

## 注入方式

组合根已创建存储、策略、代理和上下文等执行依赖后，分别创建执行服务与消费者：

```python
from core.event_processor import EventProcessor
from core.cognition_loop import CognitionLoop

processor = EventProcessor(
    event_bus=event_bus,
    agent_worker_backend=worker_backend,
    **execution_dependencies,
)
consumer = CognitionLoop(
    event_bus=event_bus,
    processor=processor,
    worker_count=1,
    poll_timeout_sec=0.5,
)
```

`execution_dependencies` 包含既有的 memory API、planner、router、orchestrator、result registry、proactive state、world model 和 system state，以及选用的 policy/context/tiered memory 等依赖。完整参数见 [EventProcessor](../core/event_processor.py)，应用组装见 [bootstrap](../app/bootstrap.py)。

新消费者调用 `processor.process_event(event, cognitive_context=...)`，并自行管理队列确认。`cognitive_context` 由调度器提供，作为数据进入既有上下文。HTTP 接口仍通过运行时入口接收事件；这不是新的绕过排队或策略的接口。

## 执行与关闭

[EVT-00](request-receipts-evt00.md) 增加请求认领与终态保留：执行服务在副作用前检查登记项，排队过期后不执行；`request_stop` 立即拒绝尚未开始的请求。首个终态不会被迟到结果或广播失败覆盖，轮询和同步读取保留结果。执行中超时/取消表示结果不确定，仍需检查真实 worker 生命周期。

- `process_event` 返回既有回复 receipt。相关请求继续经 ResultRegistry 和现有广播通道交付；回复审查结果及记忆来源标记沿用原实现。
- `reject_event` 经同一结果通道返回拒绝。`request_stop` 发出协作取消信号；此后新执行请求得到 `runtime_stopping`，不会开始写入事件或调用代理。
- `is_idle` 同时检查尚未退出的事件处理及真实 worker 占用。调用方收到超时或取消结果，不代表底层线程已经退出。这个属性是当前观测，不是容量预留。
- `CognitionLoop.stop(timeout)` 仅在消费者及下游执行退出后返回 `True`。返回 `False` 时保留存活线程引用与依赖，释放阻塞后可重试。等待线程时不会锁住运行状态查询。
- 注入的 worker 由组合根及 RuntimeController 回收。兼容构造内部创建的 worker 才由执行服务关闭；关闭前必须空闲。已关闭内部 worker 的服务不能重启。

旧的 `CognitionLoop(...)` 依赖参数、`process_event`、`reject_event` 和统计读取仍保留。旧属性读取通过兼容转发访问执行服务；新代码应直接注入服务，测试替换执行依赖时使用 `core.event_processor` 或服务实例。内部私有字段写入不构成新的兼容接口。

## 并发限制

拆分没有把多个事件的世界状态、记忆和回复变成一个跨组件事务。策略状态切换和服务计数有锁，底层 world/memory 服务保留原同步机制；旧 `system_state` 投影仍是最后写入者覆盖。多消费者模式不保证多个事件的业务执行顺序，也不提供恰好一次执行。

线程模式无法强制终止已经运行的工具。新边界保证停机结果反映实际占用，不能据此声称所有外部副作用都可取消。

契约测试见 [test_event_processor.py](../tests/test_event_processor.py)：独立执行与队列分离、策略先于执行、审查与 provenance、存储失败恢复、停机拒绝、真实阻塞线程、外部 worker 所有权及旧构造兼容。
