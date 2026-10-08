# UI／Logo 本地交付检查（REL-01）

2026-10-01。可重复检查入口已交付，当前状态为 **本地自动检查通过、整体验收未完成**。这不是生产发布或原生浏览器认证。

## 执行

2026-10-08 最新配置审阅报告为 `artifacts/ui-release/local-20261008-config-review/report.json`：475 项 JavaScript、34 项 Python、生成资产与两个源码包校验通过，组件仍为 1.1.3，源码前后一致。取消审阅、错误定位、应用／撤销及 12 组审阅布局实测见 [设计适配后续](ui-openai-adaptation.md)；前一设置导航报告保留 60 组定位与七项导航测试证据。交付后续报告 `artifacts/ui-release/local-20261008-delivery-followup/report.json` 保留两个浏览器完整包的校验；以下专项报告保留各自版本证据。

仓库根目录运行：

```powershell
python scripts/check_ui_release.py
```

默认创建带 UTC 时间的 `artifacts/ui-release/<时间>/`，保存命令日志、二阶／三阶源码交付 ZIP、源码指纹和 `report.json`。可用 `--out-dir <目录>` 指定证据位置。提供已取得的浏览器交付包后，可一并验证：

```powershell
python scripts/check_ui_release.py --browser-zip artifacts/ui-release/portable-a11y-20261001/browser-order3-dark-2048.zip
```

需要项目现有 Python／pytest 和 Node.js。校验完整 PNG 包还需要 Pillow；没有该库会明确失败，不跳过 PNG 校验。脚本不访问模型、数据库或外部服务，不自动提交 Git 或发布站点。

## 检查内容

1. 运行全部 JavaScript 回归、生成资产过期检查，以及图谱与交付校验 Python 测试。
2. 从当前唯一几何／组件生成二阶 Primary 和三阶 Dark 源码包。
3. 用独立 Python ZIP 读取器检查重复／非法路径、文件 CRC、Manifest 完整覆盖、字节数、SHA-256、品牌／组件／Token／格式版本。
4. 将包中实际组件字节与批准的来源指纹比较；重新从主几何生成标准 SVG 与 Token 并逐字节核对。不把资产和 Manifest 同时改写后的自洽哈希当作标准标识证明。
5. 核对全部二阶／三阶变体和六视角、三级微标、标准状态声明和 25% 留白规则；检查离线页所有脚本、样式和图像引用均在包内。
6. 完整 PNG 包必须包含所选尺寸图和 16／32／48px 微标，实际解码检查尺寸、Alpha、透明四角和不为空的内容。
7. 记录检查前源码指纹，结束时核对没有变化；中途变更会明确失败，不能将较早测试结果关联到较晚源码。

`status=local-checks-passed; manual-gates-unverified` 仅表示以上范围通过。`unverifiedGates` 保留原生 Chrome／Edge 保存与直接离线执行、可靠前台性能、真实 200% 缩放与屏幕阅读器验收，退出码不代表生产发布许可。校验失败时保留日志和失败报告，退出码为 1。

本轮 `artifacts/ui-release/local-20261001/report.json`：324 项 JavaScript 和 34 项 Python 测试通过；二阶／三阶源码包各 65 文件，捕获浏览器生成内容的三阶 2048px 包为 69 文件，全部通过校验。浏览器包通过读取页面已经生成的 ZIP 内容存为本地证据；这不是用户浏览器自动保存成功的证明。

验证器的 11 项 Python 回归覆盖合法包、损坏哈希、自洽重哈希后的标识／组件变更、目录缺项、未知版本及歧义路径。探针另有长测末端、冻结快照、提前停止和并行导出回归；实际性能证据见 [PERF-01](ui-logo-perf01-observations.md)。

## 尚待完成

2026-10-08 最新组件版本 1.1.3：官方设计适配的完整报告 `artifacts/ui-release/local-20261008-openai-adaptation-final/report.json` 通过 458 项 JavaScript、34 项 Python、生成资产、两个源码包及浏览器完整包。源码保持一致；布局、对比度与文字放大回退见 [OpenAI 设计适配](ui-openai-adaptation.md)。以下记录为历史版本证据。

2026-10-08 组件版本 1.1.2：交互升级的完整本地报告 `artifacts/ui-release/local-20261008-interaction-final/report.json` 通过 454 项 JavaScript、34 项 Python、生成资产、两个 67 文件源码包及 71 文件完整包，源码检查前后一致。42 组布局与阅读／导航／星图交互记录见 [UI 交互升级](ui-interaction-upgrade.md)。下文 2026-10-01 版本保留为历史证据，原生与辅助技术门槛继续待验收。

2026-10-01 的组件版本为 1.1.1：在 1.1.0 共享主题基础上，修正便携预览逐步动作反复触发播报的问题。最新稳定源码报告 `artifacts/ui-release/local-20261001-portable-a11y-final/report.json` 的 327 项 JavaScript、34 项 Python、生成资产、两个 67 文件源码包及浏览器生成的 71 文件三阶 Dark／2048px 包均通过。报告绑定检查前后相同的源码指纹，并实际解码 2048／16／32／48px PNG。详见 [统一主题的便携预览](ui-theme-system.md#便携预览接入)。1.1.0 及上文 65／69 文件记录保留为历史版本；不使用当前来源冒充旧包仍为最新。

2026-10-01 用户反馈“已连接”后，再次读取已登记 Chrome 与 Edge 的页面列表仍未取得响应。原生保存、文件来源离线执行、可靠前台十分钟测量、真实 200% 缩放及屏幕阅读器仍未验证。实际生成的 ZIP 链接捕获只证明生成内容，不证明原生落盘或离线运行；后续验收须使用同一最终版本并记录实际环境。

当前主题的组合交互已补齐本地记录：对话 16 组、品牌 16 组、双阶六视角 24 组、消息拒绝 16 组及导出／重发恢复。另修复 PNG 失败提示并验证中英文。最新稳定源码检查为 `artifacts/ui-release/local-20261001-final-interactions/report.json`，324 项 JavaScript、34 项 Python 与资产／交付包校验通过。组合矩阵和最终小修复的源码边界分别记录，详见 [组合交互验收](ui-logo-final-interactions.md)。这些证据仍限内置浏览器。

原生 Chrome 已显示在浏览器清单中，但本轮创建页面及读取同一浏览器标签状态均未取得响应，不将登记信息视作可用连接，也不据此重复创建或重启测试。内置浏览器完成的生成与生命周期证据继续单独记录。

补齐原生保存／直接离线、真实缩放／辅助技术、可靠前台性能后，再执行最终模式、主题、语言、消息／导出失败矩阵并判断 REL-01 是否通过。当前发布门槛保持未通过。

2026-10-01 主题配色后续：移除旧页面标签／状态／工具卡的独立颜色，修复主题级联覆盖。最新稳定源码报告位于 `artifacts/ui-release/local-20261001-theme-roles/report.json`，324 项 JavaScript、34 项 Python、生成资产和 65／65／69 文件包校验通过。浏览器额外核对 136 项语义配色与 42 组布局，详见 [统一主题](ui-theme-system.md)。Edge 状态请求两次复核均未取得可操作页面；这批不替代原生发布门槛。
