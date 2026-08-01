# EVA-MVSC 生产运维 Runbook v1.0

## 快速启动

```bash
# 标准启动（MVSC 关闭）
docker-compose up -d

# 启用 MVSC Pipeline
EVA_ENABLE_MVSC_PIPELINE=true docker-compose up -d

# 启用 MVSC + 消融实验
EVA_ENABLE_MVSC_PIPELINE=true EVA_ABLATION_PRESET=no_broadcast docker-compose up -d
```

## 健康检查

| 端点 | 用途 | 正常响应 |
|------|------|---------|
| `GET /health/live` | 进程存活 | `{"status":"alive"}` |
| `GET /health/ready` | 组件就绪（含MVSC状态） | `{"status":"ready", "components":{...}}` |
| `GET /api/state` | 系统状态（含MVSC tick） | `{"focus":"...", "mvsc":{...}}` |
| `GET /api/mvsc/state` | MVSC ConsciousState 详情 | `{"subject_id":"...", "tick":N}` |
| `GET /api/mvsc/lifecycle` | MVSC 生命周期 | `{"mvsc_enabled":true, "mvsc_tick":N}` |

## 监控指标

### 关键指标
- `pending_events` — 事件队列深度，>100 需关注
- `events_dropped` — 丢弃事件数，>0 需告警
- `mvsc_tick` — 认知循环计数，停止增长 = 循环卡死
- `stability_score` — 自我模型稳定性，<0.5 需关注
- `error_rate` — 认知循环错误率

### 告警阈值
| 指标 | 警告 | 严重 |
|------|------|------|
| pending_events | >50 | >200 |
| events_dropped | >0 | >10 |
| mvsc_tick 停滞 | >30s | >120s |
| error_rate | >0.05 | >0.2 |

## 日常运维

### 查看日志
```bash
docker-compose logs -f --tail=100 eva
```

### 重启
```bash
docker-compose restart eva
```

### 备份数据
```bash
tar -czf eva-backup-$(date +%Y%m%d).tar.gz data/
```

### 恢复数据
```bash
docker-compose down
tar -xzf eva-backup-YYYYMMDD.tar.gz
docker-compose up -d
```

## MVSC 管理

### 启用 MVSC（运行时不可切换，需重启）
```bash
EVA_ENABLE_MVSC_PIPELINE=true docker-compose up -d
```

### 运行时切换消融特性
```bash
# 关闭全局工作空间广播
curl -X POST http://localhost:8000/api/mvsc/ablation/toggle \
  -H "Content-Type: application/json" \
  -d '{"feature": "global_workspace", "enabled": false}'

# 查看当前消融配置
curl http://localhost:8000/api/mvsc/ablation

# 查看消融指标
curl http://localhost:8000/api/mvsc/metrics
```

### 查看 MVSC 追踪
```bash
# 通过 correlation_id 追踪事件链
curl http://localhost:8000/api/mvsc/trace/{correlation_id}
```

## 故障处理

### 症状：/health/ready 返回 not_ready
1. 检查日志 `docker-compose logs --tail=50 eva`
2. 检查数据库 `ls -la data/eva.db`
3. 重启 `docker-compose restart eva`

### 症状：mvsc_tick 不增长
1. 检查 cognition loop 日志
2. 检查 LLM API 是否可达
3. 尝试关闭 MVSC 回退到 legacy 路径

### 症状：内存持续增长
1. 检查 EventStore WAL 文件大小
2. 重启触发 WAL checkpoint
3. 检查 pending_events 队列深度

### 症状：启动失败
1. 检查 constitution.yaml 是否存在且格式正确
2. 检查 data/ 目录权限
3. 检查端口 8000 是否被占用
4. 查看启动日志 `docker-compose logs eva | head -50`

## 性能调优

| 参数 | 默认 | 建议 |
|------|------|------|
| EVA_COGNITION_WORKER_COUNT | 2 | 2-4 (按 CPU 核心数) |
| EVA_SCHEDULER_TICK_INTERVAL_SEC | 10 | 5-30 |
| EVA_QUEUE_POLL_TIMEOUT_SEC | 0.5 | 0.1-1.0 |
| EVA_RESULT_TTL_SEC | 60 | 30-120 |
| EVA_LLM_TIMEOUT_SEC | 60 | 30-120 |

## 安全

- API 认证：设置 `EVA_API_TOKEN` 环境变量
- constitution.yaml 权限应为 400（只读）
- 生产环境建议使用 PostgreSQL 替代 SQLite
- 定期审查 `/api/mvsc/verification/stats` 中的验证失败
- 审计日志可通过 EventStore 重放检查
