# 界面与子状态清单
候选：`ui-v1-20261008-g0-01`。七类界面，35 个逻辑区域，203 个源码控件入口及 8 类动态控件。来源为源码审阅，不是新一轮浏览器验收。

模板入口与所有源码控件详见 [control-inventory.json](control-inventory.json)。每个控件已归到区域；动态按钮单列。通用导航有独立归类，Jinja 条件未在此轮渲染。

| 页面 | 实际路由范围 | 模板／控制器 |
| --- | --- | --- |
| CHAT | /chat（完整服务）；预览 /chat 和 / | chat.html；chat.js,cube-avatar.js,ui-workspace.js,ui-reading.js |
| SET | /settings | settings.html；brand-system.js,brand-config.js,settings.js,ui-sections.js |
| MAP | 完整服务 / 与 /memory，index.html include；只读服务同路径 | memory_graph.html；memory_graph.js,memory_galaxy*.js,memory_diagnostics.js,goal_create.js |
| LIST | /memory/list | memory.html；memory_explorer.js |
| MON | 固定预览 /dashboard；完整 routes_ui 未登记此模板路由 | dashboard.html；app.js |
| MVSC | /mvsc | mvsc_dashboard.html；模板内联脚本,runtime_status.js |
| PROMPT | 只读服务 /chat、/settings、/memory/list；固定预览 /unavailable | service_unavailable.html；ui-theme.js |

## 区域、状态与操作影响
| surfaceId／入口 | 状态 | 焦点／键盘 | 数据与操作影响 | 可用条件 | 后续任务 |
| --- | --- | --- | --- | --- | --- |
| COMMON-01 · 共享主题 | 系统／浅／深；保存失败；跨页面更新 | 原按钮／下拉；不更改业务焦点 | 本地偏好写入，无业务 API | always | G1-04, G2-07, G4-02 |
| COMMON-02 · 共享语言 | 中／英；存储不可用 | 原语言按钮，状态文案刷新 | 本地语言偏好 | 仅含语言控制器的页面；MAP／MVSC／PROMPT 无该入口 | G1-04, G2-07, G4-02 |
| COMMON-03 · 页面导航与跳转 | 常规导航／跳到内容 | 原生链接；跳转目标 | 导航，本身不提交业务请求 | always | G1-04, G2-07 |
| CHAT-01 · 输入与提交 | 草稿／输入法组合／提交／接纳／拒绝／未确认 | 输入与消息焦点；发送不丢草稿 | POST 提交任务；remember 可写长期记忆 | always | G2-02, G2-03, G1-06 |
| CHAT-02 · 正常／深度模式 | 能力读取／normal／deep／下一请求选择 | 模式按钮，当前请求模式固定 | GET 能力；提交时固定 mode | always | G2-02, G2-03 |
| CHAT-03 · 回答、复制、重发与查询 | 长答／代码／等待／完成／已知失败／未知／迟到终态 | 操作移除时原消息；回到最新定位末条 | 剪贴板；GET 原结果；显式重发 POST 新任务 | always | G2-03, G1-05 |
| CHAT-04 · 魔方演示、模型、视角与演变 | 双阶／六视角／连续／折叠／暂停／复原／减少动态 | 折叠与 Esc 回到展开按钮，Home 复位 | 本地展示和合法动作，无任务提交 | always | G3-02, G3-03, G1-04 |
| CHAT-05 · 魔方错误恢复 | 静态替代／重试／再次失败／恢复 | 重试入口保持可达 | 保留状态日志，重建呈现器并逆序，不重发对话 | always | G2-03, G3-02, G3-03 |
| CHAT-06 · 建议卡与空对话 | 空／填入建议／重复／长度反馈 | 填入输入框；保留草稿 | 仅编辑本地草稿 | always | G1-06, G2-03 |
| CHAT-07 · 连接、运行与终态 | 连接中／连通／失败／登录更新／运行读取 | 独立状态区域；无逐 token 播报 | WS 订阅与 GET 观察 | always | G2-02, G2-07, G1-05 |
| SET-01 · 设置分类与页内定位 | 分类切换／五区定位／阅读位置／窄屏换行 | 方向键 Home End；锚点原生焦点 | 本地呈现；切运行标签暂停预览 | always | G1-04, G2-07 |
| SET-02 · 品牌预览与偏好 | 变体／双阶／视角／静态／减少动态／复原事务／错误 | 原控件；错误可重试 | 本地存储；必要时合法复原 | always | G1-04, G3-02, G3-03 |
| SET-03 · 配置读入、审阅、取消、应用与撤销 | 文件读取／编辑／非法／一致／差异／应用／取消／撤销／存储失败 | 成功审阅进差异区；错误回输入；取消回预览；应用回撤销 | 本地文件读；本地偏好写；无服务配置导入 | always | G1-04, G1-05, G4-04 |
| SET-04 · 资产库与规范 | 主标／单色／深色／微标／动作日志 | 资产按钮；原生 details | 本地选择／查看；正式导出标准状态 | always | G1-04, G4-02 |
| SET-05 · 资产导出与交付 | 生成中／成功／失败／选择变化／旧包保存 | 导出按钮／保留保存链接 | 浏览器生成与保存资产；读取本地同源组件文件 | always | G1-02, G1-03, G3-04, G5-02 |
| SET-06 · 系统运行卡片 | 预览未连接／轮询／数据／空／失败／WS 更新／折叠 | 卡片点击；实体筛选；折叠键盘待 G1 核验 | GET 运行、健康、记忆、审计；WS 订阅 | always | G2-07, G1-04 |
| MAP-01 · 目录、来源、搜索、分页与刷新 | memory／goals／加载／空／错误／搜索／分页／选择 | 节点列表；/ 搜索；移动目录 Esc 回开关 | GET 投影；本地过滤；不运行目标检查 | always | G2-04, G2-05, G1-04 |
| MAP-02 · 星图、全局／局部与相机 | 2D／3D／暂停／运转／选中／拖动／触控取消 | 画布方向键 Home 缩放；节点列表替代 | 本地绘制、相机与已加载范围 | always | G2-04, G1-04, G1-06 |
| MAP-03 · 显示设置 | 开／关／渲染选项／减少动态 | 打开首控件；Esc 返回 displayToggle | 本地展示设置 | always | G2-04, G1-04 |
| MAP-04 · 局部扩展、筛选、路径与历史 | 局部中心／筛选／邻居加载／超时／返回／路径 | 按钮与原生 details；编辑区不抢快捷键 | GET 邻居；其余本地范围和路径 | always | G2-04, G1-04 |
| MAP-05 · 节点详情、邻接与正文 | 无选择／详情加载／记录缺失／失败／清除／邻接选择 | 保持选择；详情收起与相机可用范围；实机焦点待验 | GET 详情；展示已记录来源，不补造事实 | always | G2-04, G1-05 |
| MAP-06 · 目标操作和检查历史 | active／pending_verification／interrupted／终态；历史分页；409 冲突 | 目标按钮；操作中禁用；焦点衔接待实机 | POST 显式 verify/cancel；GET 历史 | 目标节点；business_goals 可用 | G2-05 |
| MAP-07 · 历史行动与输入引用 | 未记录／未启用／加载／来源冲突／已加载 | 加载按钮；保留选择和原图 | GET ACT 图；不复制正文、不核验现内容 | ACT／durable 服务可用 | G2-06 |
| MAP-08 · 工具独立观测 | 已有核验／unknown／追加中／达到上限／冲突／超时 | 动态按钮；同 action 防重复；后续焦点待验 | GET 历史；POST 采样文件并追加记录，不重做工具动作 | Episode 且有受支持 reconciliation | G2-06 |
| MAP-09 · 关系详情和来源事件 | 选择歧义／详情／缺失引用／来源读取／失败 | 原生 dialog；端点按钮；返回落点待实机 | 本地边记录；GET 引用事件 | always | G2-04, G2-06, G1-05 |
| MAP-10 · 悬空关系诊断 | 列表／分页／失败重试／记录详情／记录过期 | 详情标题；返回原 opener；dialog 键盘待实机 | GET dangling 与 node，无修复写入 | always | G2-04, G1-05 |
| MAP-11 · 登记目标表单 | idle／busy／uncertain／success／404 可重填／校验失败 | 原生 modal autofocus；另一个目标聚焦 task；返回待验 | POST 登记；GET by-task 可能同步落库回执 | 非只读模板；切目标视图后入口可见；服务开关决定能力 | G2-05, G1-04, G1-05 |
| MAP-12 · 笔记导出与 Obsidian 链接 | 无链接／已检测镜像／导出中／失败 | 原生链接与导出按钮 | GET 生成 ZIP；外部 obsidian URI 打开；不主动改 vault | always | G2-04, G1-02 |
| MAP-13 · 沉浸面板和退出 | 进入／退出／详情抽屉／局部抽屉／尺寸变化 | Esc 优先级；焦点返回待实机 | 本地布局与相机范围 | always | G1-04, G1-06, G2-04 |
| LIST-01 · 记忆层与条目 | 层读取／层选择／加载／空／失败 | 动态层按钮保留焦点；条目展示 | GET tiers／entries | always | G2-07, G1-04 |
| LIST-02 · 跨层搜索 | 编辑／搜索中／成功／空／失败 | Enter 查询；原输入 | POST 搜索读取，不按 HTTP 方法误判为写入 | always | G2-07 |
| MON-01 · 旧监控卡片与刷新 | 轮询／空／失败／筛选／卡片折叠 | 卡片点击，键盘待验 | 多路 GET／WS；未在完整 UI 路由登记 | always | G2-07, G1-04 |
| MON-02 · 旧监控对话 | 提交／WS 或 SSE／回复／失败 | 输入与发送，模式无新选择入口 | POST 聊天；旧客户端接线另验 | always | G2-02, G2-03, G2-07 |
| MVSC-01 · MVSC 阶段、健康与开关观察 | 阶段事件／未启用／GET 数据／失败 | 仅主题按钮与静态观察；无 language 入口 | GET state/ready/ablation/runtime；WS mvsc_phase | always | G2-07, G1-04 |
| PROMPT-01 · 受限只读提示 | 提示／返回图谱 | 原生链接 | 导航，不恢复完整服务能力 | always | G2-07, G1-04 |

## 已有参考与当前缺口
- CHAT／SET／LIST 接入共享语言；MAP、目标表单、MVSC、提示页没有同等语言切换入口。主题接入和翻译覆盖分开。
- 既有七页主题／布局、对话阅读、星图显示面板、设置导航和审阅证据见 [适配记录](../../../docs/ui-openai-adaptation.md)与[交互记录](../../../docs/ui-interaction-upgrade.md)。这些是历史参考，未代表本轮逐区域运行通过。
- 目标、ACT 历史输入、工具观测、诊断、沉浸及开关关闭分支需 G2 子状态联调；真实缩放、辅助技术与手机输入需 G1。
- 各区域共同读取 `ui-system.css`／`ui-theme.js`；主题 Token 不改变 Logo 的主几何。键盘文字列记源码行为或明确待验，不作为实际播报证据。
- 来源模式：完整服务、合成预览、真实数据库只读服务、便携包分别登记；只读服务禁用目标并把对话／设置转到提示模板。
- 便携 `preview.html` 是品牌包派生产物，归属 SET-02／SET-05 的独立调用端，G1-03／G5-02 验证，不把它计为第八个产品页面。
