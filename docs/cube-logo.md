# 二阶 / 三阶魔方 Logo

对话页的「∞ 连续旋转」支持真实的 2×2×2 和 3×3×3 魔方状态，保留黑白配色。
二阶每轮执行 10 个合法随机动作，三阶执行 14 个；停顿后依次执行记录的逆操作。
复原检查比较每个块的身份、位置和朝向，不会直接重置坐标来结束循环。

## 文件与职责

- `ui/web/static/cube-state.js`：纯逻辑模块；六个面、逆时针与半转，共 18 种动作；随机序列与逆序列。
- `ui/web/static/cube-animator.js`：二阶 8 块、三阶 26 个可见块，以及 CSS 3D 层旋转；按角块、棱块、中心块分别绘制 3、2、1 张外贴面。
- `ui/web/static/cube-logo.js`：串行执行队列、动作日志、暂停、复原和循环状态机。
- `ui/web/static/cube-avatar.js`：接入视角、聊天状态、演变按钮、页面可见性与减少动态效果设置。
- `ui/web/static/logo-tokens.js`：共享几何、色彩与转动参数。
- `ui/web/static/cube-geometry.css`：聊天页与品牌工作区共用的几何面样式。
- `ui/web/static/cube-chat.css`：对话界面样式；真实转动不依赖预制关键帧。
- `ui/web/static/ui-system.css`：聊天与品牌页共用的呈现 Token，不定义新的品牌几何。
- `ui/web/static/ui-workspace.js`：响应式折叠与焦点交接，不修改状态、动作日志或暂停原因。

项目为原生 JavaScript，没有新增构建工具或渲染依赖。核心与控制器同时支持浏览器全局对象和 CommonJS，便于独立验证。

900px 以下默认显示紧凑的真实魔方与展开入口，将视角、模型和演变细节折叠。展开状态在窗口尺寸变化期间保留，折叠前若焦点位于将隐藏的控件，焦点交给展开按钮；返回宽布局时交给魔方展示区。折叠仅改变布局，连续播放、暂停和动作记录保持原状。异常恢复入口位于折叠区外，静态替代期间仍可重试或发送消息。窄屏长回复在有界消息区滚动，长代码在块内横向滚动，输入区可独立访问。验证边界见 [DS-01 验收记录](ui-logo-ds01-acceptance.md)。

## 坐标与转动

采用 CSS 坐标：X 向右、Y 向下、Z 指向观察者。每个块有稳定的 `id`，
二阶 `position` 的分量为 `-1` 或 `1`；三阶为 `-1`、`0`、`1`，不绘制中央核心块。
`orientation` 是行优先的 3×3 整数旋转矩阵。
从对应面的外侧观察，正向动作均为顺时针。

| 动作 | 轴 | 层 | CSS 角度 |
| --- | --- | --- | --- |
| R | X | +1 | +90° |
| L | X | −1 | −90° |
| U | Y | −1 | −90° |
| D | Y | +1 | +90° |
| F | Z | +1 | +90° |
| B | Z | −1 | −90° |

`'` 反转方向，`2` 表示 180°。一个动作选中当前逻辑坐标属于该外层的块：二阶 4 块，三阶 9 块。
动画期间整层块挂在同一个临时旋转组下；动画完成后再以整数矩阵更新位置与方向，
同步更新动作日志，按新状态绘制，移除临时组。逻辑从不读取 DOM transform 反推状态。
观察角旋转只改变外层相机，不修改 `CubeState`。

前五个演变阶段是结构展示姿态，不写入魔方状态。离开真实转动模式时先完成当前动作，
再逆序撤销已经提交的动作，复原后才切换展示姿态。快速切换只应用最后一次选择。

## 对话模式与模型切换

左侧「二阶魔方 / 三阶魔方」和输入区的「回答模式」双向同步，并会随每条请求发送：

| 动效模式 | 模型 | 打乱步数 | 四分之一转基础时长 / 复原时长 |
| --- | --- | --- | --- |
| 正常对话 | 二阶 | 10 | 280 / 220ms |
| 深度思考 | 三阶 | 14 | 340 / 270ms |

`ParticleController.setConversationMode("normal" | "deep")` 选择模型；
`Chat._bindForm()` 在提交前锁定模式，调用 `setResponding(mode)`，并把同一个
`mode` 放进 `/api/chat`、`/api/chat/sync` 和 `/api/chat/stream` 请求体。
原有 `setResponding()` 在该请求的模型上启动真实层转动，回复结束后逆序还原；
回复进行中切换选择只排队到下一条消息，不会改变当前请求的魔方或推理策略。
六种视角、六个演变阶段、拖动、暂停和减少动态效果均支持两种模型。
正在转动时切换模型，会等待当前动作及已提交步骤的逆序复原完成，再更换几何；连续快速选择只应用最后一次。
页面刷新默认回到二阶。服务端会在执行任务时再次读取该模式，并根据实际适配器
选择策略：OpenAI 已知 GPT-5.4/5.5 及日期变体使用 `reasoning_effort`，
DeepSeek 使用 `thinking`，Claude Sonnet/Opus 4.6 使用 adaptive thinking；
未知模型使用可审计的提示词策略，不冒充原生推理。`GET /api/chat/modes` 返回当前
配置下的能力说明，结果回执也保留 `mode` 与 `mode_info`。界面只显示公开的等待/完成
状态，不展示或伪造模型的私有思维链。

演示模式（`python scripts/preview_cube_chat.py --port 8776`）会返回两种模式的
固定文本与能力元数据，不调用模型；正式服务的原生参数由适配器负责，且不会升级
已配置的模型名称。

## 独立使用

按顺序加载 `logo-tokens.js`、`cube-state.js`、`cube-animator.js`、`cube-logo.js` 和 `cube-geometry.css`。
根节点必须处于 `transform-style: preserve-3d` 的场景中，使用同样的 `.cube-stage[data-phase="5"]`
样式禁用单个块的 CSS transition；合法层转动由 Web Animations API 插值 CSS transform。

```js
const state = new CubeLogic.CubeState(3); // 省略参数默认为二阶；支持 2 或 3。
const animator = new CubeRendering.CubeAnimator(rotorElement, state);
const logo = new CubeLogoSystem.CubeLogo({
  state,
  animator,
  scrambleLength: 10,
  moveDuration: 280,
  solveDuration: 220,
  loop: true,
  onChange: status => console.log(status.stage, status.move),
});

// 根据需要启动；无限循环的 Promise 在停止并复原后才完成。
logo.play().catch(console.error);
logo.pause();
logo.resume();
await logo.reset(); // 完成当前动作，逆序复原，不直接跳回初始帧。

await logo.scramble(); // 停留在打乱状态，保留日志。
await logo.solve();
await logo.play({ loop: false }); // 仅执行一轮。
console.log(logo.state.snapshot(), logo.state.isSolved());
```

重复调用播放不会创建重叠任务。`solve()` / `reset()` 会停止剩余打乱和循环，
将当前正在执行的合法动作完成后，再撤销实际执行过的序列。
每次复原使用 `inverseSequence(history)`；半转的逆操作仍为该面的半转。

`pause(reason)` / `resume(reason)` 支持 `user`、`hidden`、`drag` 等独立原因。
只有全部原因解除才继续原动画；暂停不会推进逻辑状态。
`setReducedMotion(true)` 完成当前转动，并以零时长逆操作恢复完整静态 Logo，停止循环。
尺寸通过外层容器缩放控制，与魔方逻辑无关。

## 异常与恢复

对话页动效失败时显示同阶数的标准静态 Logo，保留真实状态与已提交日志。消息输入、发送与回复不因此停止；重试不会重新提交聊天请求。品牌工作区采用相同恢复契约，仍允许从独立标准状态导出资产。

「重试动效」调用 `CubeLogo.recover({ createAnimator })`：等待失败任务退出，按日志重放得到预期状态，与当前每个块的身份、位置和朝向核对，再使用原有状态对象重建呈现器并执行严格逆序。旧呈现器的块与动画资源随后释放。重复重试共享一个任务，恢复期间的新结构选择只在复原后应用最后一次。

```js
await logo.recover({
  createAnimator: state => {
    rotorElement.replaceChildren();
    return new CubeRendering.CubeAnimator(rotorElement, state);
  },
});
```

工厂必须沿用传入状态，不改写坐标或日志。没有工厂的 `recover()` 使用当前呈现器重新提交可信状态；错误状态下 `solve()` 也走日志核对。日志不一致、重建失败或逆操作失败时保持错误，保留剩余动作用于检查或再次重试，不显示复原成功。错误状态直接播放／打乱会拒绝，避免自动循环重试。

恢复保留隐藏／拖动暂停原因；显式复原解除用户暂停。减少动态效果在错误期间不会清空日志，重试时按零时长逆操作复原。对话恢复后应用最新请求的展示形态；品牌预览恢复后停在标准状态，等待显式播放。静态替代图层不代表内部状态已经复原。

## 视觉与动效参数

`CubeRendering.geometryFor(order)` 统一单块尺寸、模块间隙和圆角：二阶为 62 / 4 / 5，三阶为 40 / 4 / 3.3。
两种模型的完整外廓均为 128px；`CubeRendering.Geometry` 保留二阶默认值，实例使用 `animator.geometry`。
并将尺寸写入 CSS 变量。结构展示和真实转动共用中心间距，切入连续模式时不改变整体尺寸。
`CubePresentation` 定义默认视角（X −24°、Y −36°）、透视距离（1100）及悬停幅度（X ±2°、Y ±3°）。
悬停只作用于独立的外层容器；待机无循环动画，离开指针、开始拖动、切换阶段和减少动态效果时均归零。

材质位于共享的 `cube-geometry.css`，布局位于 `cube-chat.css`：暖白正面、石墨侧面、轻微面渐变和柔和接触阴影。
`--cube-white`、`--cube-graphite`、`--cube-edge` 管理材质，`--scene-scale` 管理响应式显示比例。
这是 CSS 3D 的品牌视觉材质，不包含实时物理光照。坐标和轨道默认隐藏，展开「查看运动细节」后显示。
侧栏与欢迎区使用 `eva-mark.svg`，浏览器图标使用简化的 `eva-favicon.svg`。

转动使用 `[1.04, 0.98, 1, 0.96]` 的轻微时长变化，半转增加 20% 时长；每步仍严格落到合法整数状态。
保留原控制器的停顿和末次复原减速。启动时外层轻倾 1.2°，复原后观察视角平滑回到默认品牌姿态。
这些变化不修改打乱序列、动作日志或逆序复原算法。

## 验证

```powershell
node --test tests/js/test_cube_state.cjs tests/js/test_cube_animator.cjs tests/js/test_cube_logo.cjs tests/js/test_cube_chat.cjs
```

验证包括：所有动作的可逆性、四次四分之一转、位置与朝向的整数约束、外贴面法向、
两种阶数的随机序列逆序复原、同一实例 100 轮无漂移、18 种动作的动画终点与逻辑矩阵一致、
中途暂停/复原、页面隐藏、减少动画、快速切换和异常后的动作日志一致性。
这些是状态与渲染契约测试，不代表在所有设备上测得 60 FPS。

本地界面预览：`python scripts/preview_cube_chat.py --port 8776`，打开 `/chat`。
预览中的聊天回复为测试文本；魔方引擎与正式页面共用实现。
