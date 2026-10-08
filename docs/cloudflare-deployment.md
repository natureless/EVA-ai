# EVA Cloudflare 部署

目标入口：`https://eva.bianhua.cc.cd`。Windows 本机保留 EVA、SQLite 和 Obsidian；Cloudflare 提供 HTTPS、邮箱登录和命名隧道。

## 1033 故障恢复（2026-10-06）

13:48 的连接器日志显示，cloudflared 查询 `_v2-origintunneld._tcp.argotunnel.com` 时 DNS 超时，边缘发现失败后以非零状态退出。排查时 Tunnel 计划任务为 Ready、最近退出码为 1，没有连接器进程；EVA 后端 PID 17984 仍正常监听 `127.0.0.1:8000`。这解释了 14:10 的公网 1033；本次没有证据表明域名配置或应用数据有误。

原始失败日志已保存在本机受保护的 `.cloudflare/backups/`。DNS 查询恢复后，重新启动既有 Tunnel 任务恢复了连接。随后更新并重新启动连接器运行脚本：Tunnel 非零退出后按 5、10、20、40、60 秒退避，之后每 60 秒继续重试；如果此前连接器运行至少 120 秒，退避从 5 秒重新开始。正常退出不重启。只保留上一轮 stdout/stderr 及最近失败的 `Tunnel.retry.json`，均位于原有私有目录。App 仍使用原有计划任务重启方式。

14:18 复核两个任务均 Running，连接器 readiness 为 200、活动连接为 4，公网首页返回 302 到既有 Access 登录域名。13 项接口验收通过，包括源站 readiness、静态资源、420 节点/658 连线的图谱及节点详情、邻居、Obsidian ZIP 导出、匿名访问保护和 WebSocket 重连。EVA 后端 PID 与启动时间保持不变；DNS、Access 策略和 Tunnel ingress 没有修改。

证据：[故障恢复接口报告](../artifacts/cloudflare-1033-recovery-2026-10-06.json)。三项隔离 PowerShell 回归验证了超过三次失败后的恢复、退避上限、日志保留、正常退出以及 App 退出码传递；脚本语法和该测试文件的 Ruff 检查通过。生产仅演练了连接器重启并重新建立四条连接，没有注入生产网络故障，也没有将此次接口验证表述为新的浏览器视觉验收。

任务仍由当前用户登录触发，并非无需登录的系统服务；机器关机、休眠或用户注销后的可用性不由此重试逻辑解决。

## 当前发布状态（2026-09-25）

最新前端更新：页内刷新保留图谱视图与节点选择；诊断弹窗打开时暂停背景绘制。两个文件备份后发布，生产仍为原后端进程；图谱、readiness、静态文件版本与匿名 Access 拦截均复核正常。独立浏览器交互及 35 项 JavaScript / 1,362 项 Python 测试通过，公网浏览器交互仍因控制连接超时未确认。范围与证据见 [图谱文档](obsidian-memory-graph.md) 和 [graph-refresh-2026-09-25.json](../artifacts/graph-refresh-2026-09-25.json)。

同日后续前端更新：关系诊断新增“查看实体”和“查看来源事件”，沿用已有记录读取接口，未重启后端。全量 Python 1,362 项、JavaScript 29 项、Ruff 和隔离 preflight 通过；生产接口与新文件版本已复核。公网列表已显示新按钮，但详情点击后的浏览器控制超时，公网详情画面尚未确认。详情功能、本地浏览器验收和边界见 [图谱文档](obsidian-memory-graph.md)，文件备份与结果见 [diagnostic-drilldown-2026-09-25.json](../artifacts/diagnostic-drilldown-2026-09-25.json)。下文为此前后端发布记录。

关系诊断已发布到正式入口，生产后端此前的 404 已解决。EVA 继续运行在本机，沿用既有 Cloudflare Tunnel 和 Access；本次没有修改 DNS、Access 策略或 Tunnel 配置。

发布证据：[production-release-2026-09-25.json](../artifacts/production-release-2026-09-25.json)。发布前全量 Python 测试 1,361 项、JavaScript 测试 26 项及仓库 Ruff 均通过。已备份 6 个持久状态文件及 373 个源文件；两个 SQLite 备份完整性检查均为 `ok`。备份位于 `.cloudflare/backups/release-20260925T022647Z/`。

确认运行队列与待处理结果为空、保存快照后，向旧进程发送控制台中断；日志确认应用正常完成关闭，再由既有计划任务启动生产进程 PID 31776，仍只监听 `127.0.0.1:8000`。本次重启加载了当前工作区的 Python 改动，并非仅加载诊断路由。Tunnel 未重启，收尾时两个计划任务均为 Running，EVA 与 Tunnel readiness 均通过。

| 发布后验收 | 结果 |
| --- | --- |
| 图谱与诊断 | 图谱接口 200，采样为 420 节点、660 连线；诊断接口 200、206 条。公网登录浏览器实际验证 1–50 与 51–100 分页 |
| 公网 SSE | 登录会话请求返回 200 / `text/event-stream`，收到测试回复、完成回执及结束标记，无错误 |
| 公网 WebSocket | 登录会话建立连接，ping 收到 pong |
| 笔记导出 | 公网返回 200 / ZIP；本机导出另行通过 ZIP 完整性检查，包含 421 个条目 |
| 身份与缓存 | 公网匿名首页、诊断、导出、WebSocket 入口均 302 到 Access；本机匿名诊断 401。记忆响应继续使用 `private, no-store` |

SSE 验收提交了一条标记测试消息（`source_event_id=deploy-check-20260925-sse-01`，`remember=false`），事件记录保留。尚未完成手机实机访问、Windows 注销后重新登录恢复或崩溃恢复演练；本次正常重启不等同于这些验证。

## 发布前核查（2026-09-25）

实际架构已是：浏览器 → `eva.bianhua.cc.cd` / Cloudflare Access → 命名 Tunnel `eva-bianhua` → 本机 `127.0.0.1:8000` EVA → 本机 SQLite；Obsidian 笔记为本机显式同步生成的单向镜像。不是待接入方案，也没有迁往 Workers 或云服务器。

本次只读复核证据：[deployment-alignment-2026-09-25.json](../artifacts/deployment-alignment-2026-09-25.json)。报告不包含令牌或登录邮箱。

| 项目 | 发布前证据 |
| --- | --- |
| DNS 委派 | Cloudflare zone `bianhua.cc.cd` 为 `active`；Google 和 Cloudflare 公共 DNS 均返回 NOERROR，NS 为 `ali.ns.cloudflare.com`、`porter.ns.cloudflare.com`。早期 SERVFAIL 已不是本次状态 |
| 域名路由 | `eva.bianhua.cc.cd` 为代理 CNAME，指向已部署的命名 Tunnel |
| Tunnel | API 状态 `healthy`，4 条连接；本机指标同为 4，readiness 200。Ingress 指向 `http://127.0.0.1:8000`，Access JWT 校验开启且 AUD 与本地配置一致 |
| Access | 应用 `EVA Memory` 覆盖该 hostname；一条 Allow 策略，精确匹配一个邮箱，与本地允许邮箱一致；应用限定的身份提供方为 One-time PIN |
| 匿名访问 | 公网 `/`、`/api/memory/graph`、`/api/memory/graph/dangling`、`/ws` 均返回 302 到 Access 登录页；直连本机记忆 API 返回 401。HTTP `/ws` 登录拦截不等于本轮完成了 WebSocket 升级测试 |
| 生产服务 | PID 19812 监听 `127.0.0.1:8000`，进程始于 9 月 20 日。带本机应用令牌读取图谱为 200，420 节点、660 连接、206 条关系缺少实体；响应为 `private, no-store`，Cloudflare CDN 缓存头为 `no-store` |
| 常驻任务 | `EVA Cloudflare App` 与 `EVA Cloudflare Tunnel` 均 Running，记录的最近启动为 9 月 20 日。配置是当前用户登录触发、异常重试三次，不是无需用户登录的系统服务；本轮没有重启或故障演练 |
| 公网图谱交互 | 本会话已实际验证加载、放大/缩小、滚轮、适应窗口、列表节点与画布节点点击；这是 9 月 19 日记录之后补齐的验证 |
| Obsidian | 代码已使用 vault 名称 + 相对笔记路径，无 Windows 绝对路径；目标设备仍需有对应笔记库。本轮未验证手机 Obsidian 同步 |

### 发布前版本差异（现已解决）

9 月 25 日新增“关系诊断”在 `127.0.0.1:8774` 的只读预览中返回 200，但生产 `127.0.0.1:8000/api/memory/graph/dangling` 返回 **404**。生产首页和新的 `memory_diagnostics.js` 均已返回 200，首页已包含该入口。

原因边界：生产与开发使用同一目录，模板和静态文件可即时被读取；Python 后端仍由 9 月 20 日启动的进程承载，新路由尚未加载。因此准确状态是“前端文件已更新、生产后端待发布”，不能笼统写成“整个 EVA 尚未上线”或“新功能已完整上线”。

最初的只读对齐没有修改 DNS、Access、Tunnel，也没有重启生产服务。随后已完成备份、全量检查及生产重启，诊断、SSE、WebSocket 和导出的生产验收结果见本文顶部；实际手机访问仍未验证。此处保留发布前差异作为历史记录。

## 历史部署记录（2026-09-19）

- 已部署 `https://eva.bianhua.cc.cd`。zone 为 `active`，`eva` 的 proxied CNAME 指向已创建的命名隧道。
- 实际团队域名为 `round-math-6deb.cloudflareaccess.com`。Access 应用 `EVA Memory` 覆盖完整 hostname，只有一条精确邮箱 Allow 规则，强制使用 One-time PIN。策略已回读确认。
- 按用户选择切换到新账户令牌，原令牌已从本地部署配置替换、不再使用。虽然新令牌从 R2 页面提供，实际权限查询确认它具有 Tunnel / Access 管理及账户令牌编辑权限。已通过其管理权限补齐仅当前 zone 的 DNS Read、DNS Write、Zone Read，保留原有授权；DNS 读写已验证成功。S3 访问密钥未使用。
- 命名隧道 `eva-bianhua` 当前 API 状态为 `healthy`，本机 cloudflared 指标显示 4 条活动连接；连接器启用 Access JWT 校验。生产 EVA 在 `127.0.0.1:8000` 运行，配置检查通过。
- `EVA Cloudflare App` 与 `EVA Cloudflare Tunnel` 两个当前用户计划任务已注册并处于 Running。登录触发器与异常重试已配置；真实重新登录及异常重启仍未单独演练。
- 公网首页、记忆 API、WebSocket 入口的匿名 HTTP 请求均返回 302 到实际 Access 团队登录页；本机匿名记忆请求返回 401。签名公钥端点和连接器 readiness 返回 200，生产 WebSocket ping 通过。用户登录后，Cloudflare Access 审计确认 2026-09-12 19:39:20 UTC 登录获准，隧道记录了成功的 HTTP 200 响应；本机图谱复核为 420 节点、709 连线。浏览器控制连接仍超时，未直接检查公网图谱的视觉效果及交互。
- 已完成本地 Access JWT 验证、HTTP / SSE / WebSocket 兼容，以及 Obsidian 按 vault 名称打开笔记。
- 验证结果：全量 Python 回归 1357 项通过，JavaScript 测试 23 项通过，Ruff 和 PowerShell 脚本语法通过。测试与隔离验证期间，原始 5 个数据文件哈希保持不变；之后正式生产启动已正常迁移数据库并持续写入记忆和快照。
- 已完成 5 个文件的部署前备份；SQLite 副本完整性检查通过。隔离副本完成 006/007/008 迁移，完整应用启动、图谱 420 节点读取和 WebSocket ping 通过。验证使用 mock LLM，没有调用真实模型。

## 发布顺序

2026-09-27 已补充[固定 P0 验收清单](p0-acceptance.md)和可重复执行的源站/公网匿名检查脚本。15 项实际接口检查通过，含源站 SSE、断开后的同任务查询、WebSocket 重连及 ZIP 完整性。公网浏览器控制连接不可用，已登录交互仍待复验，不能将本轮结果标为 P0 全部完成。

1. 在 Cloudflare 控制台完成 Zero Trust 首次开通，选择 Free；记录实际团队域名。若 API 仍拒绝写入，检查令牌对该账户的 Access Apps and Policies、Identity Providers、Organizations 权限，以及 Tunnel 编辑和该 zone 的 DNS 编辑权限。
2. 创建 `self_hosted` Access 应用，覆盖 `eva.bianhua.cc.cd` 全部路径。Allow 规则只包含账户邮箱的精确地址，使用 One-time PIN。不要添加 Everyone 或 Bypass。记录应用 AUD。
3. 创建 remote-managed 命名隧道；连接器令牌仅放在受 Windows ACL 保护的 `.cloudflare/tunnel-token`。
4. 隧道 ingress 指向 `http://127.0.0.1:8000`，启用 `originRequest.access.required`，绑定实际 `teamName` 和 `audTag`；最后一条为 `http_status:404`。
5. 复制 [.env.cloudflare.example](../.env.cloudflare.example) 为 `.env.cloudflare`。填写实际团队 HTTPS 地址、AUD、邮箱和公网 origin；单独生成至少 32 字符随机 EVA_API_TOKEN。不要使用 Cloudflare 管理令牌充当应用令牌。
6. 用 SQLite backup API 备份当前数据库，同时备份 profile、self_model、snapshot；在副本上验证首次启动迁移。生产启动会正常更新记忆与快照。
7. 运行 `python -m app.cloudflare_server --check`。配置检查不加载应用、不打开数据库。通过后启动单个源站进程与隧道。
8. 确认 Access 应用和 Allow 规则已生效，再创建 `eva` 的 proxied CNAME 指向 `<tunnel UUID>.cfargotunnel.com`。
9. 验证匿名 HTTPS 请求只能到登录页面；登录后验证图谱、API、SSE 和 WebSocket；最后验证用户登录 Windows 后自动运行。

## 本机运行

```powershell
python -m app.cloudflare_server --check
python -m app.cloudflare_server
```

入口固定监听 `127.0.0.1:8000`，固定 production，单 Uvicorn 进程，不信任任意转发头。Access 未完整配置或应用令牌缺失时拒绝启动。

[Start-EvaCloudflare.ps1](../scripts/Start-EvaCloudflare.ps1) 为当前 Windows 用户注册两个计划任务，登录后启动，异常退出最多重试三次。脚本可用 `-Action Status` 查看，`-Action Stop` 停止并禁用自动启动。首次注册和启动已验证，真实重新登录及异常重启仍需实机验证；需要电脑开机、用户登录且网络可用。Stop 会结束进程，应在没有进行中的任务时使用；持久记忆以 SQLite 为准，最近尚未持久化的状态可能丢失。

## 身份边界与数据

- Origin 使用 Cloudflare 公钥验证 RS256 签名、issuer、audience、有效期、subject 和精确邮箱，不信任独立邮箱请求头。
- 浏览器使用 Access 会话；服务器验证后仅在内部 ASGI 请求中注入 API token。前端不保存管理令牌或应用长效令牌。
- Access 启用后，静态资源、健康检查、文档、导出及 WebSocket 也需要身份。健康检查需在本机携带 `X-API-Token`；已有脚本 API token 在本机仍可使用。公网还要经过 Access 与 tunnel 的 JWT 检查。
- 浏览器写入与 WebSocket 升级检查 Origin；响应禁用浏览器及 Cloudflare CDN 缓存。
- `.env`、`.env.cloudflare`、`.cloudflare/`、`.tools/` 被 Git / Docker 构建排除。管理令牌只供部署，正常运行只需要 tunnel token 和独立应用 token。
- Obsidian 链接使用 `vault` 名称及相对笔记路径。另一设备需安装 Obsidian，并有同名且同步过的 vault。网页展示不依赖安装 Obsidian。

9 月 19 日收尾复核通过当时账户令牌实际读取了 Tunnel、Access 应用和 DNS CNAME，匿名公网请求返回 302 到 Access 登录页。当时浏览器控制连接不稳定，未验证视觉交互；9 月 25 日的新增验证及版本差异见本文顶部。

参考：[Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/)、[Access One-time PIN](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)、[Zero Trust organization API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/organizations/methods/create/)。
