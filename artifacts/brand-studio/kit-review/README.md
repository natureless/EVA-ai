# EVA Brand Kit / 品牌交付包

打开 preview.html 可离线查看所有静态资产，并运行真实的二阶 / 三阶动态组件。没有外部字体、网络依赖或模型调用。

## 资产

- svg/：二阶和三阶，Primary、Monochrome、Dark、Light，六种观察角；共 48 个标准 SVG。
- icons/：16、32、48px 二阶三视角微标。
- selected/eva-selected.svg：当前选择 2 × 2 / primary / iso。
- PNG：selected/eva-selected.png, icons/favicon-16.png, icons/favicon-32.png, icons/favicon-48.png
- tokens.json：品牌 Token；manifest.json：参数、尺寸、文件清单与 CRC32 校验值。
- components/：原生 JavaScript 动态组件和几何样式；preview.html 同时提供接入示例。

## 接入

按顺序加载 components/logo-tokens.js、cube-state.js、cube-animator.js、cube-logo.js，并引用 eva-logo.css。
根节点使用 cube-stage eva-logo 类和 data-phase="5"，内部结构参照 preview.html。
创建 CubeState、CubeAnimator 和 CubeLogo 后显式调用 play({loop:true})；solve() 严格逆序撤销已提交动作。
正常对话采用二阶，深度思考采用三阶。响应完成、结构切换及退出演示前应等待 solve()。
遵循 prefers-reduced-motion，并在页面隐藏时 pause('hidden')，显示时 resume('hidden')。
从 SVG / PNG 取得静态品牌资产，不截取打乱中的动态帧。

## 使用规范

L1 动态主标：160px 以上；L2 产品标识：48–160px；L3 微标：16–48px。
保持整体比例；四周留白至少为标识宽度的 25%。SVG / PNG 已包含留白。
允许主题变体、单色、等比缩放、静态展示和合法转动。
禁止拉伸、独立缩放模块、随意修改间隙与圆角、添加霓虹或复杂纹理。
动态演示结束必须复原。CRC32 用于检测文件损坏，不作为来源或身份验证。
