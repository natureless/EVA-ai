# G0 发现与后续处置
源码问题与未验证风险分别登记；不是生产缺陷复现报告。

| 编号 | 优先级／证据等级 | 发现 | 后续 | 处置 |
| --- | --- | --- | --- | --- |
| G0-F01 | P0 · confirmed-contract-gap | 聊天 maxlength=32000，API ChatRequest.text max_length=4000；客户端没有同名 4000 限制。 | G2-02, G2-03, G4-01 | 在隔离联调验证 4000／4001／长草稿；后续修复需保持草稿及明确错误，不自动截断。 |
| G0-F02 | P0 · confirmed-side-effect-contract | GET goals/by-task 和相关 get_goal 会 _reconcile/record_receipt；GET api/state 更新内存计数。 | G2-05, G2-07 | 修正接口分类；同步已有回执不等于执行新检查。该行为本身不判为代码缺陷。 |
| G0-F03 | P1 · confirmed-locale-coverage-gap | MAP、目标创建、MVSC、受限提示没有共享 I18N 脚本和 langBtn；不能宣称全部子界面完整中英。 | G1-04, G2-04, G2-05, G2-07, G4-02 | 登记实际语言支持范围；子状态文案纳入统一语言改造或明确延期，现有主题接入不代表翻译完成。 |
| G0-F04 | P1 · confirmed-route-scope | dashboard.html 仅固定预览 /dashboard 路由；完整 routes_ui 默认 / 返回 index.html（星图）。 | G2-07, G5-04 | 旧监控保留为待确认交付入口；不新增路由来补造已有产品能力。 |
| G0-F05 | P0 · integration-risk-unverified | 新聊天／星图走 EvaHttp；settings、app、memory_explorer、MVSC 内联仍有直接 fetch。认证失效处理一致性未实测。 | G2-03, G2-07, G4-01 | 隔离环境测试 401、登录页及 JSON 错误；源码差异不是已经复现的认证缺陷。 |
| G0-F06 | P1 · keyboard-risk-unverified | 运行和旧监控 data-card 卡片标题绑定 click；当前模板该入口非原生 button，键盘折叠需实测。 | G1-04, G4-02 | 登记键盘覆盖风险；不把静态检查当作屏幕阅读器实测。 |

## 源码依据
- G0-F01：[ui/web/templates/chat.html:104](../../../ui/web/templates/chat.html)；[app/api_routes/routes_chat.py:35](../../../app/api_routes/routes_chat.py)
- G0-F02：[app/api_routes/routes_goals.py:61](../../../app/api_routes/routes_goals.py)；[app/api_routes/routes_goals.py:109](../../../app/api_routes/routes_goals.py)；[app/api_routes/routes_chat.py:330](../../../app/api_routes/routes_chat.py)
- G0-F03：[ui/web/templates/memory_graph.html:144](../../../ui/web/templates/memory_graph.html)；[ui/web/templates/mvsc_dashboard.html:1](../../../ui/web/templates/mvsc_dashboard.html)；[ui/web/templates/service_unavailable.html:1](../../../ui/web/templates/service_unavailable.html)
- G0-F04：[scripts/preview_cube_chat.py:77](../../../scripts/preview_cube_chat.py)；[app/api_routes/routes_ui.py:14](../../../app/api_routes/routes_ui.py)
- G0-F05：[ui/web/static/http.js:4](../../../ui/web/static/http.js)；[ui/web/static/settings.js:32](../../../ui/web/static/settings.js)；[ui/web/static/memory_explorer.js:28](../../../ui/web/static/memory_explorer.js)
- G0-F06：[ui/web/static/settings.js:248](../../../ui/web/static/settings.js)；[ui/web/templates/settings.html:122](../../../ui/web/templates/settings.html)
