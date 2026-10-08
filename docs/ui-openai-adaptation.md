# OpenAI 设计原则的 EVA 适配

2026-10-08，Components 1.1.3。适用于 EVA 独立产品的对话、记忆、设置及运行界面。保留 EVA 魔方识别与合法转动系统。

## 官方来源与适用范围

已查阅 [OpenAI UI guidelines](https://developers.openai.com/plugins/concepts/ui-guidelines) 的 Typography、Color、Spacing & layout、Accessibility，以及 [OpenAI Design Guidelines](https://openai.com/brand/) 的标识比例与留白说明。

UI 指南面向 ChatGPT 内的应用。EVA 是独立界面，因此采用系统字体、清晰层级、中性表面和可访问性的原则；不直接套用宿主的输入框、卡片操作数量或禁止卡片内部导航等限制。品牌指南约束 OpenAI 的标识；本项目保持自己的魔方主标，并未使用 Blossom、Wordmark 或 OpenAI Sans。

以下具体字号、列宽、间距和留白值均为 EVA 的产品决定，不能称为 OpenAI 官方 Token。

## 已实现

| 适配原则 | EVA 实现 |
| --- | --- |
| 采用系统字体，减少结构文字变化 | 共享字体改为 system-ui 与系统回退；辅助标签采用统一 caption 字号，取消装饰性等宽字体与字距 |
| 中性、清晰的内容表面 | 对话与默认品牌预览移除背景渐变；输入表面与发送按钮使用主题 Token；状态色继续表达实际状态 |
| 对话优先、按需展示 | 桌面对话区占比增加；全部屏幕默认折叠魔方模型、视角和演变控制，可随时展开；几何参数放入原生 details |
| 品牌一致性 | 同一主几何导出全部资产；保留明确的主题变体背景和 3D 表面光照；比例、间隙、留白与合法动作未改变 |
| 可访问性 | 保留键盘焦点和折叠时焦点返回；增加强制色彩下焦点与选中轮廓；大文字空间不足时允许页面增长与滚动 |

`ui-system.css` 是共享呈现来源；`ui-workspace.js` 仅控制展示状态，不替换魔方实例、不修改转动日志，也不提交草稿。手机短窗口可能采用文档滚动；常规 390×844 窗口保留底部输入布局。

品牌 1.0.0、Token schema 1、包格式 2 保持不变；组件升级至 1.1.3。来源指纹、变更记录与生成文件已更新。

## 本地验证

- [完整检查报告](../artifacts/ui-release/local-20261008-openai-adaptation-final/report.json)：458 项 JavaScript、34 项 Python、生成资产、两个 67 文件源码包及浏览器生成的 71 文件完整包通过，检查前后源码一致。
- [布局矩阵](../artifacts/ui-openai-adaptation/20261008/layouts.json)：七个界面 × 浅深主题 × 320／768／1440 宽度，共 42 组，无页面横向溢出，主题匹配。
- [共享色彩对比度](../artifacts/ui-openai-adaptation/20261008/token-contrast.json)：28 组文字／背景 Token 配对，最低 4.81:1。此结果不代表全部画布、图像或整个产品的 WCAG 认证。
- [折叠交互](../artifacts/ui-openai-adaptation/20261008/disclosure.json)：实际点击展开、切换侧面、收起后，草稿、视角及 8 个模块保留，焦点回到展开按钮。
- [手机检查](../artifacts/ui-openai-adaptation/20261008/phone.json)：两种主题、两种语言、两种尺寸共 8 组，无横向溢出；短窗口允许滚动，发送按钮在聚焦后可见，未提交消息。
- [文字放大](../artifacts/ui-openai-adaptation/20261008/text-resize.json)与[小屏发送回退](../artifacts/ui-openai-adaptation/20261008/text-resize-phone.json)：临时把根字号从 16px 调为 32px，三个核心界面无横向溢出，小屏发送按钮可滚动到导航栏上方。检查后已重新加载移除该临时样式。
- [主标指纹比较](../artifacts/ui-openai-adaptation/20261008/master-comparison.json)：相对前一版本的 geometry、tokens、rules 三项 SHA-256 均相同。

截图：[对话](../artifacts/ui-openai-adaptation/20261008/chat.png)、[品牌页](../artifacts/ui-openai-adaptation/20261008/settings.png)、[小屏文字放大](../artifacts/ui-openai-adaptation/20261008/text-resize-phone.png)。

浏览器 ZIP 是通过预览页面生成并读取保存链接字节取得，已独立校验全部资源、PNG 透明留白及来源；不作为原生浏览器下载或真实离线运行证据。文字放大检查是 DOM 字号模拟，不等同于原生浏览器 200% 缩放。原生下载／离线、真实手机软键盘、屏幕阅读器和长期前台性能门槛仍待实机验收。

## 交付选择与键盘操作后续 · 2026-10-08

品牌交付区同时显示当前选择和上次文件的选择。打包使用点击时的不可变快照；更改结构、变体、视角或 PNG 尺寸后，保存链接明确标为上次文件，并提示重新生成。回到相同选择时自动恢复一致状态。文件名包含结构、变体、视角与尺寸。静态／减少动态偏好不改变导出的标准资产，因此不会误报文件过期。

生成新包时保留已有文件；生成失败不删除上一份可用 ZIP，成功才替换。相同的交付状态不重复写入 live region。对话魔方控制展开后，在演示控制或魔方上按 Escape 可收起并把焦点还给展开按钮；输入区及 IME 组合事件不被截获。

本轮仅修改产品工作区脚本、样式和模板，便携组件文件及主标规则未改变，组件版本仍为 1.1.3。

- [最新完整检查](../artifacts/ui-release/local-20261008-delivery-followup/report.json)：468 项 JavaScript、34 项 Python、两个 67 文件源码包及两个 71 文件浏览器完整包通过，源码前后一致。
- [实际竞态](../artifacts/ui-delivery-followup/20261008/race.json)：暂缓本地品牌来源请求，在打包时将三视角／2048px 改为正面／512px；ZIP 及文件说明保持点击时选择，当前选择和保存旧文件提示正确。
- [失败保留](../artifacts/ui-delivery-followup/20261008/failure.json)与[成功重试](../artifacts/ui-delivery-followup/20261008/retry.json)：模拟来源读取失败后旧链接可见且字节相同；恢复正常请求后生成新的正面／512px 文件，状态回到一致。测试请求拦截已恢复并移除。
- [布局](../artifacts/ui-delivery-followup/20261008/layouts.json)：差异提示在浅深主题、中英文、320／768／1440 宽度共 12 组均无横向溢出。
- [状态去重](../artifacts/ui-delivery-followup/20261008/live-regions.json)：五次相同语言刷新没有重写两个交付 live region；这是 DOM 观测，尚非屏幕阅读器验收。
- [实际 Escape 操作](../artifacts/ui-delivery-followup/20261008/escape.json)：演示按钮收到 Escape 后收起、焦点返回，草稿与 8 个模块保留，消息数为零。

截图：[交付区](../artifacts/ui-delivery-followup/20261008/delivery.png)、[选择不一致提示](../artifacts/ui-delivery-followup/20261008/stale-selection.png)。浏览器包通过保存链接字节取得，原生下载与离线执行门槛继续待验收。

## 设置导航后续 · 2026-10-08

五个原生锚点入口随阅读位置显示当前区域。定位留白依据导航实际高度与桌面容器内边距计算，适应语言切换、窄屏换行与文字放大。页面末尾资产库与交付区同时可见时，使用原生锚点焦点识别定位目标；手动滚动不强制选中最后一区，不修改编辑焦点或草稿。资产卡长标题支持换行。

- [锚点矩阵](../artifacts/ui-section-navigation/20261008/anchors.json)：浅深主题 × 中英文 × 320／768／1440 宽度 × 五个定位，共 60 组通过；目标区域无遮挡，当前提示匹配，无页面横向溢出。长标题换行修复后另验下面四组大字号布局。
- [键盘与编辑状态](../artifacts/ui-section-navigation/20261008/focus.json)：Enter 定位到显示配置，焦点进入目标区域；编辑中通过开发测试滚动到末尾保留输入焦点与未应用草稿，运行标签往返也保留草稿。
- [文字放大](../artifacts/ui-section-navigation/20261008/text-resize.json)：320px 窄屏、根字号 32px、浅深主题与中英文四组，定位留白约 16px，无横向溢出。临时字号已在检查后重载清除；这是 DOM 模拟，非原生 200% 缩放。
- [最终完整检查](../artifacts/ui-release/local-20261008-section-navigation/report.json)：自动检查与源码包校验；七项导航行为测试覆盖滚动、换行、末尾定位、焦点保留、隐藏恢复及卸载。

截图：[设置导航](../artifacts/ui-section-navigation/20261008/settings.png)、[小屏文字放大](../artifacts/ui-section-navigation/20261008/text-resize.png)。本轮只修改产品界面，便携 Logo 组件仍为 1.1.3；原生下载、离线、屏幕阅读器与长期前台性能门槛仍待实机验收。

## 配置审阅后续 · 2026-10-08

新增取消审阅入口，保留输入文本与已生效设置。键盘审阅成功将焦点移入有标题的差异区域；失败标记输入框无效并返回输入焦点。编辑清除错误状态，相同配置说明无需应用。差异表采用语言对应的变体名称与 `scope="row"` 行标题；相同差异与语言不重复重建行。

- [实际交互](../artifacts/ui-config-review/20261008/interaction.json)：错误定位、编辑清错、成功审阅、取消保留文本与焦点返回、相同配置禁用应用，以及应用／撤销恢复已生效设置均验证。更改当前 PNG 尺寸后差异表更新，输入文本保留；五次相同语言刷新产生零次差异表 DOM 修改。这是浏览器 DOM／键盘验证，非屏幕阅读器认证。
- [布局矩阵](../artifacts/ui-config-review/20261008/layouts.json)：浅深主题 × 中英文 × 320／768／1440 宽度，共 12 组审阅区域无横向溢出，取消入口语言匹配。
- [大字号](../artifacts/ui-config-review/20261008/text-resize.json)：320px 英文界面临时根字号 32px，无横向溢出，审阅区域可聚焦；测试样式重载清除。
- [完整检查](../artifacts/ui-release/local-20261008-config-review/report.json)：475 项 JavaScript、34 项 Python、生成资产与两个源码包校验通过，检查前后源码一致。组件仍为 1.1.3。

截图：[配置审阅](../artifacts/ui-config-review/20261008/review.png)。本次应用示例已撤销，临时观测器和草稿已移除；原生下载、离线与辅助技术验收仍保留原有待验收状态。
