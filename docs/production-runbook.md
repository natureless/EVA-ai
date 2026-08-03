# EVA 生产运行手册

状态：稳定运行时的规范文档。MVSC 默认关闭，仅作为可选实验能力。

## 部署边界

当前支持的生产拓扑是：

```text
单个 EVA API 进程
-> 进程内 EventBus / ResultRegistry / Scheduler
-> SQLite + JSON + Snapshot 持久化
-> 有界线程 AgentWorker
```

不要设置多个 Uvicorn worker，也不要横向启动多个 EVA 副本。当前队列、结果注册表和
调度器不是共享组件。多副本部署必须先完成外部事件代理、共享结果存储、调度器选主和
PostgreSQL 迁移。

## 启动

PowerShell：

```powershell
$env:EVA_API_TOKEN = "replace-with-a-long-random-token"
docker compose up --build -d
docker compose ps
```

本地进程：

```powershell
$env:EVA_ENV = "production"
$env:EVA_API_TOKEN = "replace-with-a-long-random-token"
python scripts/preflight.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

生产模式缺少 `EVA_API_TOKEN` 时必须停止部署。密钥只通过环境变量或密钥管理器提供，
不得写入仓库、镜像、日志、命令历史或文档。

## 建议配置

| 变量 | 建议值 | 说明 |
| --- | --- | --- |
| `EVA_ENV` | `production` | 启用生产约束 |
| `EVA_API_TOKEN` | 随机长令牌 | API 身份认证 |
| `EVA_COGNITION_WORKER_COUNT` | `1-2` | 事件处理并发，先从 1 开始 |
| `EVA_AGENT_WORKER_COUNT` | `2-4` | 进程内 Agent 线程数 |
| `EVA_AGENT_EXECUTION_TIMEOUT_SEC` | `120` | Agent 软超时 |
| `EVA_ENABLE_CODE_TOOL` | `false` | 高风险能力，默认关闭 |
| `EVA_ENABLE_NETWORK_TOOLS` | `false` | 高风险能力，默认关闭 |
| `EVA_ENABLE_MVSC_PIPELINE` | `false` | 实验管线，默认关闭 |
| `EVA_STORAGE_BACKEND` | `sqlite` | 当前单实例路径 |

线程超时不能强制终止正在运行的 Python 代码。真正的 Agent 隔离依赖开发计划中的
进程/容器 worker 后端；在此之前，不应向不可信用户开放代码工具。

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8000/health/summary
Invoke-RestMethod http://127.0.0.1:8000/metrics
```

重点观察：

- `pending_events` 和事件最老等待时间
- `events_dropped` 与处理错误数
- cognition loop 最近心跳和延迟
- agent worker 的 `submitted/completed/timed_out`
- SQLite、Snapshot、Scheduler、Connector 就绪状态
- LLM 调用错误、重试、延迟和费用相关指标

建议初始告警：

| 信号 | 警告 | 严重 |
| --- | --- | --- |
| 事件队列深度 | 持续高于 50 | 持续高于 200 或继续增长 |
| 丢弃事件 | 大于 0 | 10 分钟内大于 10 |
| cognition 心跳 | 30 秒无更新 | 120 秒无更新 |
| worker 超时率 | 5 分钟高于 5% | 5 分钟高于 20% |
| Snapshot | 2 个周期未更新 | 无可验证快照 |

阈值需要用真实负载校准，不应直接作为长期 SLO。

## 日志

```powershell
docker compose logs -f --tail 200 eva
```

日志必须包含关联 ID，但不得包含 API 密钥、Webhook secret、认证头、完整外部 payload
或未脱敏用户内容。出现凭据时立即撤销并轮换；清理日志副本不能代替平台侧撤销。

## 备份

需要一起备份：

- SQLite 数据库及其 WAL/SHM 一致性状态
- `data/profile.json`、`data/self_model.json`
- `data/snapshots/`
- 向量索引及其可重建元数据
- Connector checkpoint/cursor
- 配置模板，但不包含密钥值

推荐流程：

1. 暂停外部流量和 Connector。
2. 触发 Snapshot 并验证完整性。
3. 停止 EVA 或使用 SQLite 在线备份 API 获取一致副本。
4. 打包持久卷并记录应用版本、数据库版本和时间。
5. 在隔离目录执行恢复演练，而不是只验证压缩包存在。

不要在运行中的 SQLite 文件上直接做普通文件复制并假定其一致。

## 恢复

```powershell
docker compose down
# 恢复经过验证的数据卷/文件
docker compose up -d
```

恢复后依次检查：

1. `/health/ready`
2. Snapshot 完整性和版本兼容性
3. 最近事件、记忆、World Graph 和 Persona 状态
4. Connector checkpoint，避免重复摄入
5. 一次 mock chat 和一次真实 LLM smoke test

## 常见故障

### Chat 已接受但没有回复

1. 使用 `/api/chat/result/{task_id}` 查询。
2. 检查 EventBus 队列和 cognition loop 心跳。
3. 检查 Agent worker 超时/取消统计。
4. 检查 LLM provider、配额、网络和重试日志。
5. 只在确认状态已持久化后重启。

### `/health/ready` 不通过

1. 查看 `/health/diagnostic` 和容器日志。
2. 检查数据目录权限、数据库和 Snapshot。
3. 检查端口占用及是否误启动了第二个实例。
4. 使用 `/health/recover` 前先读取具体恢复动作影响。

### 队列持续增长

1. 暂停低优先级 Connector 输入。
2. 检查慢 Agent、LLM 超时、存储锁和 worker 饱和。
3. 不要盲目提高 worker 数；先确认 CPU、内存和外部 API 限制。
4. 保存诊断、事件和 trace 样本后再重启。

### SQLite 锁或损坏

1. 停止所有 EVA 进程。
2. 保留原始数据库及 WAL/SHM 副本用于诊断。
3. 从最近验证备份恢复，或使用 SQLite 官方恢复工具。
4. 不要直接删除数据库、WAL、SHM 或所谓锁文件。

### GitHub Connector 无事件

检查仓库范围、令牌权限、Webhook HMAC、delivery ID、轮询 checkpoint、速率限制和
`/api/github/status`。Connector 只应产生标准事件，不应直接调用 Agent。

## MVSC 实验模式

仅在隔离环境启用：

```powershell
$env:EVA_ENABLE_MVSC_PIPELINE = "true"
docker compose up --build -d
```

通过 `/api/mvsc/*` 检查状态、trace、完整性、消融和缓存。出现异常时先关闭 feature
flag 回到稳定管线。MVSC 不应成为第二套权威 Memory、World 或 Policy 状态。

## 发布检查清单

1. Ruff、全量测试、preflight 全部通过。
2. 数据和 Snapshot 迁移已在副本上验证。
3. 认证令牌和外部凭据已轮换/注入，仓库无明文密钥。
4. 代码/网络工具仍为预期状态。
5. 备份、恢复、回滚各演练一次。
6. 单实例约束、资源限制和告警已配置。
7. OpenAPI、架构文档和开发计划与发布版本一致。
