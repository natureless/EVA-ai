# G2-01 隔离联调准备

状态：通过准备检查；18 项实际 API／启动检查通过。候选 `ui-v1-20261008-g0-01`，界面与关联 API 字节仍匹配 G0 基线。

当前证据：[环境](run-g3a196yw/environment.json)、[准备检查](run-g3a196yw/readiness.json)、[准备过程与失败记录](attempts.json)。此前失败来自联调脚本自身：未设置隔离用户目录、局部类型注解被解释为查询参数、SQLite 只读 URI 未正确解析。分别修正后重新创建测试库，原失败记录保留，不能据此判产品图谱故障。

服务采用真实 `app.main`、认证、运行时、SQLite、模板、聊天接纳／回执和 WebSocket；模型固定 MockLLM。UI 为当前源码的字节一致副本，非固定预览回复服务器。每次运行生成新 `run-*` 目录，数据／日志／快照／配置写入该目录；不读取实际 .env、不继承 EVA／模型／代理参数、不复制生产数据库。仅允许回环网络，Python 审计约束不等同于操作系统沙箱。

本轮实际检查：缺凭据返回 401；使用本地测试令牌后运行时启动、6 个实际模板返回 HTML；正常／深度能力均为 mock；空库图谱可读；目标和 ACT 未启用返回 503；一条 normal 请求经过实际流水线并可查询同一回执；4001 字符被实际 API 返回 422；WebSocket ping 返回 pong；容器数据库路径位于隔离目录。运行时 `ready` 为真实组件结果，`llm=false` 与启动诊断 degraded 原样记录，不描述为真实模型健康。

运行方法（在 `D:\EVA-ai`）：

```powershell
python artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py
# 可选：单独运行回环服务，使用新的隔离目录，Ctrl+C 结束
python artifacts/ui-acceptance/ui-v1-20261008-g0-01/G2-01/run_isolated_service.py --serve --port 8784
```

`--serve` 使用实际认证中间件；HTTP／WS 客户端需发送 `X-API-Token: ui-acceptance-local-fixture-token`。这是公开的本地测试夹具，不是用户凭据。此轮通过记录来自 TestClient，未运行 TCP serve 模式或浏览器页面操作，不能用这些检查代替原生 UI 结果。

所有可选业务开关为 false，实际值与关闭分支已记录；目标／Episode／durable／ACT 开启状态的专门数据与环境仍需 G2-05／06 前准备。本轮未验证 Cloudflare 登录、远端模型、双模式完整 WS／SSE 交互或魔方终态，这些归后续任务。

下一批：G2-02 四组请求与固定模式；G2-03 不确定结果与竞争回执。原生 G1-01 的工具阻断见 [环境记录](../G1-01/environment-check.json)，不重复调用已中止的 Windows 操作。
