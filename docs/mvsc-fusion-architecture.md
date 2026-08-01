# EVA-MVSC (DCS) 融合架构总览

## 一句话

> **EVA-DCS = Substrate + Kernel + Cognition Loop + World/Self Model + Workspace + Memory + AgentOS + Governance + Lifecycle + MVSC Evaluation**

## 从当前项目到EVA-DCS的演进路径

```
当前 EVA v0.2                     →    EVA-MVSC DCS v0.1
─────────────────────────────────────────────────────────
Event (简单事件)                   →    EventEnvelope (完整溯源)
EventBus (线程安全队列)            →    EventStore (append-only + 重放)
CognitionLoop._process_one()       →    PipelineCognitionLoop (13阶段)
PolicyEngine StateMachine (4态)    →    RuntimeMode (8态)
SelfModelStore (单JSON)            →    SelfModel (6子模型)
-                                  →    BodyModel (数字身体)
-                                  →    Attention (可解释注意力)
-                                  →    Workspace (全局工作空间)
-                                  →    Metacognition (元认知)
-                                  →    Verifier (行动验证)
-                                  →    IntentParser (意图解析)
Planner (混合职责)                 →    Planner (DAG)
ImportanceScorer                   →    ValueModel (独立价值模型)
-                                  →    Ablation Framework (消融实验)
```

## 已实现的新组件

| 文件 | 内容 |
|------|------|
| `packages/contracts/events.py` | EventEnvelope + EventFamily (16个事件族) |
| `packages/contracts/state.py` | ConsciousState + BodyState + Goal + Plan + ContentCandidate 等 |
| `packages/contracts/protocols.py` | CognitiveModule + StateRepository + 15个协议 |
| `packages/models/self_model.py` | SelfModel (6子模型: Identity/DigitalBody/Boundary/Agency/Capability/Narrative) |
| `packages/kernel/event_store.py` | EventStore (append-only + 确定性重放 + 检查点) |
| `packages/cognition/loop.py` | PipelineCognitionLoop (13阶段) + Attention + Workspace + Metacognition + DecisionEngine |
| `packages/mvsc_lab/ablations.py` | AblationConfig + AblationMetrics + AblationRunner |
| `configs/experiments.yaml` | 特性开关 + 消融预设 + 关键实验定义 |

## 与现有代码的关系

所有新组件设计为与现有代码**共存**而非**替换**：

1. **EventEnvelope ↔ Event**: `from_legacy_event()` 转换函数
2. **PipelineCognitionLoop ↔ CognitionLoop**: 通过 feature flag 切换
3. **SelfModel ↔ SelfModelStore**: `SelfModel.from_legacy()` 兼容
4. **EventStore ↔ EventBus**: EventStore 是新的持久化层，EventBus 保留为内存队列

## 下一阶段任务

参考 `docs/mvsc-implementation-roadmap.md` 的完整路线图。

## 工程验收标准

参考MVSC框架第十四节的验收标准矩阵。
