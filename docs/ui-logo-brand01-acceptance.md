# BRAND-01 版本与来源验收

日期：2026-09-30。本项代码和本地验收已交付；M1／M2 的原生浏览器、离线、缩放与辅助技术待验收项继续保留，M3 尚需 BRAND-02，不据此宣称整套系统完成。

## 当前交付

- `brand-identity.js` 共享包格式 2、品牌 1.0.0、Token schema 1、组件 1.0.0；新增规范化几何／Token／实际配色规则 SHA-256 与组件源文件指纹。
- Manifest 为每个实际文件保留字节数、CRC32，并新增 SHA-256；标准状态导出与留白规则不变。离线页和工作区显示实际版本，ZIP 文件名包含版本，包内有 CHANGELOG.md。
- 导航资产生成增加 brand-source.json；检查可发现规则、版本和源码过期。规则变化未增加品牌版本或试图降版时拒绝生成覆盖；源码变化被内容指纹记录。
- 浏览器和 CLI 在导出前核对来源记录，不允许旧页面规则与新源码混合。未知格式、Token schema 或不支持主版本明确拒绝；旧格式 1 明确标为无来源指纹，不猜测迁移。
- SHA-256 是内容追踪，不提供来源认证；规范化算法为 eva-sorted-json-v1，不宣称 RFC JCS。

## 自动与命令检查

新增 11 项有效回归：SHA-256 与独立 Node crypto 在 UTF-8／二进制／分块边界一致；规范化键顺序与数组语义；规则、几何、配色及源码的独立变化；真实浏览器 UMD 与 CLI 相同包字节；Manifest 每个源文件和产物的实际哈希；旧／未知版本及非法指纹；缓存来源混用；生成产物过期；历史主版本比较和降版拒绝。

生成命令在隔离临时目录中实际演练：正常生成／检查通过；只改源码后 --check 返回 1；改动作 Token 后 --check 返回 1，同版本生成拒绝；明确增加品牌补丁版本后生成与检查通过。临时目录只在验证路径位于本批指定制品目录后清理，没有修改运行中的品牌规则。

全量 JavaScript 298 项通过，0 失败；`build_brand_assets.cjs --check` 和改动脚本语法检查通过。本批未改后端契约，未运行 Python 全量回归。命令入口：

```powershell
node --test tests/js/*.cjs
node scripts/build_brand_assets.cjs --check
node scripts/export_brand_kit.cjs --order 3 --variant dark --view bottom --size 256 --out artifacts/brand-studio/brand01/eva-brand-1.0.0-3x3-dark-source.zip
```

## 实际包与页面证据

内置浏览器本地预览使用三阶、Dark、底部、256px，生成 69 文件；CLI 同参数生成 65 文件。浏览器文件从已生成的保存链接中捕获字节，**不是原生浏览器保存成功的证据**。校验记录见 [archive-verification.json](../artifacts/brand-studio/brand01/archive-verification.json)，页面记录见 [browser-acceptance.json](../artifacts/brand-studio/brand01/browser-acceptance.json)。

- 独立 Python zipfile 解码与 CRC 检查通过；每个 Manifest 条目的字节数、CRC32、SHA-256 与真实文件匹配；全部组件文件与来源指纹匹配。
- 两包版本与来源完全一致。公共静态 SVG、Token、组件源码、样式、离线页和变更记录字节一致；README 只因 PNG 清单不同而有差异；浏览器多 4 个 PNG，尺寸分别为 256、16、32、48px。
- 页面未知版本注入显示明确错误，保存链接保持隐藏，所选三阶／Dark 未被覆盖；来源规则指纹不匹配时也拒绝打包并提示重新生成／刷新。
- 中文与英文版本说明使用相同版本定义；320px 英文卡片宽度与 scrollWidth 相同，根节点未横向溢出。
- 一次保存链接等待在短观察窗口内超时，随后同一任务的 busy=false／链接可见／69 文件状态确认成功；没有重启服务或将超时当作产品失败。

恢复默认显示偏好、中文浅色并清除临时 viewport；一次性来源替代和注入经重载清除。CLI 与捕获的浏览器包仅证明生成内容、版本和校验有效，不替代最终离线运行、原生下载或性能证据。

下一步为受控配置导入、差异预览、显式应用与撤销（BRAND-02）；全局顺序仍见 [专项实施明细](ui-logo-development-plan.md) 与 [v3 第 11 节](source-review-and-roadmap-v3.md#11-ui-与-logo-专项执行顺序2026-09-30)。
