# BASE-02：实际接线与离线运行基线

2026-09-12。状态：已实现；验证记录见 [基线采样](../artifacts/base02-runtime.json) 和 [回归记录](../artifacts/base02-validation.json)。

## 实际接线

| 配置 | HTTP EventBus 消费者 | 处理服务 | MVSC 状态 |
| --- | --- | --- | --- |
| 两个开关均关闭 | `core.cognition_loop.CognitionLoop` | `core.event_processor.EventProcessor` | disabled |
| `EVA_ENABLE_MINIMAL_BRAIN=true` | `packages.minimal_brain.kernel.MinimalBrainKernel` | 同一个 EventProcessor | disabled |
| `EVA_ENABLE_MVSC_PIPELINE=true` | CognitionLoop，实际模式仍为 legacy | 同一个 EventProcessor | 适配器装配成功为 attached，失败为 failed |
| 两个开关均开启 | 配置验证拒绝启动 | — | — |

HTTP 先在 ResultRegistry 预留回执，再向 EventBus 发布。所选消费者领取请求，调用共享处理服务；处理服务写入终态，HTTP/SSE/WS 读取或传递该回执。Minimal 内部节点有独立调度周期，但一次请求的业务处理仍通过现有 EventProcessor。MVSC 的 AdaptedCognitionLoop 需要实验调用方显式驱动；开关没有把它接成 HTTP 消费者。

`GET /api/runtime` 增加实际 `consumer_type`、`processor_type`，并区分 `requested` 开关与 `extensions.mvsc` 的 `requested/status/attached/is_http_consumer`。`mode` 与生命周期观察来自 RuntimeController。配置接口同时展示实际模式，`mvsc.enabled` 保留为配置开关兼容字段。状态、健康接口中的 MVSC 字段改用装配状态，不再以特性开关推断“正在执行”，也不把未观测的实验 tick 填成 0。

聊天页和 MVSC 实验页读取上述实际运行状态，保留的 dashboard 模板也接入同一显示组件（当前没有 dashboard 页面路由）；获取失败显示“未知”，消费者停止显示“未接收请求”。MVSC 装配失败不会掩盖仍在工作的 legacy 消费者。`GET /api/config/defaults` 从 Settings 字段定义读取默认值，不再读取当前环境变量。

## 路径与生命周期

MVSC EventStore 现在使用 `settings.data_dir / mvsc_event_store.db`。构造后续组件失败时关闭已创建的数据库；关闭失败向生命周期所有者返回失败，保留重试责任。

审查发现，原有 4 个 MVSC 组件测试使用空 FakeSettings，依赖固定 `data/mvsc_event_store.db` 路径，且部分测试没有关闭连接。这些测试现已显式使用临时 data_dir 并在结束后关闭连接。它们验证显式调用的实验循环；文档中原来“HTTP 全链路”的说法已修正。此次没有修复或推断旧 MVSC 数据库的历史内容。

## 可重复采样

```powershell
python scripts/benchmark_runtime.py --output artifacts/base02-runtime.json
```

固定输入和关键配置在 [runtime-v1.json](../configs/baselines/runtime-v1.json)：2 条预热、12 条问候消息、并发 4、MockLLM、无向量模型、单个线程 agent worker、关闭网络/代码工具与 GitHub 轮询。每种配置分延迟/内存两轮，每轮使用新的 Python 进程、空临时工作目录与数据库；不加载项目 `.env`。外部 socket/DNS 访问被拒绝，允许 Windows asyncio 所需的数字 loopback 地址。请求经应用的真实 FastAPI lifespan、路由和中间件，使用 ASGI TestClient，无真实 TCP 客户端开销。

回执轮询从 100 ms 开始退避到 500 ms，保留默认 HTTP 限流。初版 5 ms 高频轮询曾触发限流；当前退避参数已固定在清单中。该采样包含轮询延迟，不能用它推断处理器的纯执行速度。

报告包含输入清单哈希、源码哈希、Python/依赖版本、路径规范化后的有效配置及哈希、逐条采样、分位数和拒绝/超时计数。临时路径替换为占位符，报告不采集用户输入、运行数据或环境凭据。

| 指标 | 定义与限制 |
| --- | --- |
| `ack_ms` | ASGI POST 开始到接纳响应 |
| `admission_to_execution_ms` | 单调时钟测量：预留回执到处理服务首次领取；包含 EventBus 与 Minimal 内部调度等待，不是 EventBus 单个队列的纯驻留时间 |
| `admission_to_terminal_ms` | 预留回执到产生终态，不包含客户端轮询等待 |
| `http_terminal_ms` | POST 开始到 GET 获得终态；包含轮询退避、路由和中间件 |
| `python_peak_bytes` | 单独 tracemalloc 轮次，从 bootstrap 前到预热/工作负载结束的 Python 分配峰值；不含 import、原生分配或进程 RSS |

ResultRegistry.lookup 及 HTTP 结果轮询提供 `timing`。尚未开始或未完成的边界用 null；等待中的 `waiting_age_ms` 随当前单调时钟增长。重复领取不会重置开始时间。超时终态时间是注册表观测并生成终态的时间，不是外部动作实际结束时间。

小样本分位数采用 nearest rank，12 条样本的 p95 等于最大值。当前样本显示 Minimal 有额外调度等待，MVSC 装配也增加初始化/分配成本。这不能证明架构优劣、认知能力或生产吞吐；尚无真实模型、长会话、大图、持续压力、跨重启或网络传输基线。

## 本次样本

| 配置 | 启动 ms（未分析内存） | 接纳至执行 p95 ms | HTTP 获得终态 p95 ms | 独立轮次 Python 分配峰值 MiB |
| --- | ---: | ---: | ---: | ---: |
| legacy | 47.31 | 31.00 | 120.67 | 1.26 |
| minimal | 62.44 | 109.00 | 310.23 | 1.74 |
| mvsc_attached_legacy | 95.27 | 15.00 | 121.63 | 2.18 |

每轮 12 条请求均完成，无队列满拒绝或回执超时。数字只描述这次固定 Mock 样本；两轮使用独立进程，内存轮次的延迟不能与上表混用。回归结果为 1247 项 Python 测试、8 项 JavaScript 行为测试通过，静态检查通过。全量回归及最终采样后，受保护的 5 个默认数据文件哈希均未改变。

## 验收与后续

[接线测试](../tests/test_runtime_wiring.py) 覆盖正常 legacy、Minimal、MVSC 装配成功与失败：API 标识、所选实例身份、实际处理调用、唯一 trace 与回执对应关系。Minimal 下 legacy 消费线程未启动；MVSC 下实验 `run_once` 没有消费 HTTP 请求。另覆盖构造清理、关闭重试和可控时钟的等待/执行边界。[界面行为测试](../tests/js/test_runtime_status.cjs) 检查模式文字、失败状态及过期显示清除。

下一项为 EVT-01：版本化事件契约、老事件适配、未知版本拒绝与稳定来源标识。随后 STA-01 建立状态 revision/StateDelta 提交规则，EVT-02 建立持久 inbox/outbox。BASE-02 与 EVT-00 均未提供跨重启的可靠请求接纳或外部动作 exactly-once 保证。

浏览器验证：在独立临时数据的 MVSC 配置预览中，聊天页与 `/mvsc` 均显示“Legacy · 运行中 · MVSC 扩展已装配”，实验 tick/phase 未伪造数值。已检查聊天页截图的提示与输入区布局；未覆盖移动端和所有主题。该预览只读展示，未接入原有运行数据。
