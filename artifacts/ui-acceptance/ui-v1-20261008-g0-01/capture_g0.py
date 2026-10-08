"""Read-only G0 capture. No app startup, network, database, credentials or Git mutations."""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
CANDIDATE = OUT.name
REPORT = 'artifacts/ui-release/local-20261008-config-review/report.json'
PLAN = 'docs/ui-logo-task-breakdown-20261008.md'

def read(path):
    return (ROOT / path).read_text(encoding='utf-8')

def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def write(name, value):
    (OUT / name).write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def ref(path, needle=None):
    lines=read(path).splitlines()
    line=next((i for i,s in enumerate(lines,1) if needle and needle in s),1)
    if needle and line==1 and needle not in lines[0]:
        raise ValueError(f'Missing source reference: {path}: {needle}')
    return {'path':path,'line':line}

def mdref(value):
    return f"[{value['path']}:{value['line']}](../../../{value['path']})"

PAGES = {
    'CHAT': ('chat.html','/chat（完整服务）；预览 /chat 和 /','chat.js,cube-avatar.js,ui-workspace.js,ui-reading.js','CHAT-01'),
    'SET': ('settings.html','/settings','brand-system.js,brand-config.js,settings.js,ui-sections.js','SET-02'),
    'MAP': ('memory_graph.html','完整服务 / 与 /memory，index.html include；只读服务同路径','memory_graph.js,memory_galaxy*.js,memory_diagnostics.js,goal_create.js','MAP-02'),
    'LIST': ('memory.html','/memory/list','memory_explorer.js','LIST-01'),
    'MON': ('dashboard.html','固定预览 /dashboard；完整 routes_ui 未登记此模板路由','app.js','MON-01'),
    'MVSC': ('mvsc_dashboard.html','/mvsc','模板内联脚本,runtime_status.js','MVSC-01'),
    'PROMPT': ('service_unavailable.html','只读服务 /chat、/settings、/memory/list；固定预览 /unavailable','ui-theme.js','PROMPT-01'),
}
SURFACES=[]

def surface(id,page,title,members,states,focus,effect,tasks,gate='always',refs=None):
    SURFACES.append(dict(surfaceId=id,page=page,title=title,members=members.split(),states=states,
        focus=focus,effect=effect,tasks=tasks.split(),gate=gate,sourceRefs=refs or []))

surface('COMMON-01','*','共享主题','themeBtn themePreference','系统／浅／深；保存失败；跨页面更新','原按钮／下拉；不更改业务焦点','本地偏好写入，无业务 API','G1-04 G2-07 G4-02')
surface('COMMON-02','*','共享语言','langBtn','中／英；存储不可用','原语言按钮，状态文案刷新','本地语言偏好','G1-04 G2-07 G4-02','仅含语言控制器的页面；MAP／MVSC／PROMPT 无该入口')
surface('COMMON-03','*','页面导航与跳转','','常规导航／跳到内容','原生链接；跳转目标','导航，本身不提交业务请求','G1-04 G2-07')
surface('CHAT-01','CHAT','输入与提交','chatForm chatInput rememberToggle sendBtn streamToggle','草稿／输入法组合／提交／接纳／拒绝／未确认','输入与消息焦点；发送不丢草稿','POST 提交任务；remember 可写长期记忆','G2-02 G2-03 G1-06',refs=[ref('ui/web/static/chat.js','async _sendViaWS'),ref('app/api_routes/routes_chat.py','class ChatRequest')])
surface('CHAT-02','CHAT','正常／深度模式','','能力读取／normal／deep／下一请求选择','模式按钮，当前请求模式固定','GET 能力；提交时固定 mode','G2-02 G2-03')
surface('CHAT-03','CHAT','回答、复制、重发与查询','chatMessages chatLatest','长答／代码／等待／完成／已知失败／未知／迟到终态','操作移除时原消息；回到最新定位末条','剪贴板；GET 原结果；显式重发 POST 新任务','G2-03 G1-05')
surface('CHAT-04','CHAT','魔方演示、模型、视角与演变','cubePanel cubeDisclosure cubeDetails cubeOrderControls cubeStage resetCube playEvolution cubeInspector','双阶／六视角／连续／折叠／暂停／复原／减少动态','折叠与 Esc 回到展开按钮，Home 复位','本地展示和合法动作，无任务提交','G3-02 G3-03 G1-04')
surface('CHAT-05','CHAT','魔方错误恢复','cubeRecovery cubeRetry','静态替代／重试／再次失败／恢复','重试入口保持可达','保留状态日志，重建呈现器并逆序，不重发对话','G2-03 G3-02 G3-03')
surface('CHAT-06','CHAT','建议卡与空对话','chatEmpty','空／填入建议／重复／长度反馈','填入输入框；保留草稿','仅编辑本地草稿','G1-06 G2-03')
surface('CHAT-07','CHAT','连接、运行与终态','wsIndicator runtimeStatus','连接中／连通／失败／登录更新／运行读取','独立状态区域；无逐 token 播报','WS 订阅与 GET 观察','G2-02 G2-07 G1-05')
surface('SET-01','SET','设置分类与页内定位','brandTab runtimeTab','分类切换／五区定位／阅读位置／窄屏换行','方向键 Home End；锚点原生焦点','本地呈现；切运行标签暂停预览','G1-04 G2-07')
surface('SET-02','SET','品牌预览与偏好','brandPreviewCard brandReset brandRecovery brandRetry','变体／双阶／视角／静态／减少动态／复原事务／错误','原控件；错误可重试','本地存储；必要时合法复原','G1-04 G3-02 G3-03')
surface('SET-03','SET','配置读入、审阅、取消、应用与撤销','brandConfigCard','文件读取／编辑／非法／一致／差异／应用／取消／撤销／存储失败','成功审阅进差异区；错误回输入；取消回预览；应用回撤销','本地文件读；本地偏好写；无服务配置导入','G1-04 G1-05 G4-04')
surface('SET-04','SET','资产库与规范','brandAssetLibrary brandJournal','主标／单色／深色／微标／动作日志','资产按钮；原生 details','本地选择／查看；正式导出标准状态','G1-04 G4-02')
surface('SET-05','SET','资产导出与交付','brandDelivery brandExportSvg brandExportPng brandExportTokens','生成中／成功／失败／选择变化／旧包保存','导出按钮／保留保存链接','浏览器生成与保存资产；读取本地同源组件文件','G1-02 G1-03 G3-04 G5-02')
surface('SET-06','SET','系统运行卡片','runtimeWorkspace entityFilter','预览未连接／轮询／数据／空／失败／WS 更新／折叠','卡片点击；实体筛选；折叠键盘待 G1 核验','GET 运行、健康、记忆、审计；WS 订阅','G2-07 G1-04')
surface('MAP-01','MAP','目录、来源、搜索、分页与刷新','explorer graphSource graphSearch tierFilters goalPaging nodeList reloadGraph explorerToggle','memory／goals／加载／空／错误／搜索／分页／选择','节点列表；/ 搜索；移动目录 Esc 回开关','GET 投影；本地过滤；不运行目标检查','G2-04 G2-05 G1-04')
surface('MAP-02','MAP','星图、全局／局部与相机','graphStage memoryCanvas globalView localView graphDimension orbitPlayback cameraOptions graphNavigation galaxyAngle galaxySystem resetCamera zoomIn zoomOut fitGraph','2D／3D／暂停／运转／选中／拖动／触控取消','画布方向键 Home 缩放；节点列表替代','本地绘制、相机与已加载范围','G2-04 G1-04 G1-06')
surface('MAP-03','MAP','显示设置','displayPanel displayToggle','开／关／渲染选项／减少动态','打开首控件；Esc 返回 displayToggle','本地展示设置','G2-04 G1-04')
surface('MAP-04','MAP','局部扩展、筛选、路径与历史','localControls localPath','局部中心／筛选／邻居加载／超时／返回／路径','按钮与原生 details；编辑区不抢快捷键','GET 邻居；其余本地范围和路径','G2-04 G1-04')
surface('MAP-05','MAP','节点详情、邻接与正文','inspector','无选择／详情加载／记录缺失／失败／清除／邻接选择','保持选择；详情收起与相机可用范围；实机焦点待验','GET 详情；展示已记录来源，不补造事实','G2-04 G1-05')
surface('MAP-06','MAP','目标操作和检查历史','goalDetail','active／pending_verification／interrupted／终态；历史分页；409 冲突','目标按钮；操作中禁用；焦点衔接待实机','POST 显式 verify/cancel；GET 历史','G2-05','目标节点；business_goals 可用')
surface('MAP-07','MAP','历史行动与输入引用','actionInputs loadActionInputs','未记录／未启用／加载／来源冲突／已加载','加载按钮；保留选择和原图','GET ACT 图；不复制正文、不核验现内容','G2-06','ACT／durable 服务可用')
surface('MAP-08','MAP','工具独立观测','toolChecks','已有核验／unknown／追加中／达到上限／冲突／超时','动态按钮；同 action 防重复；后续焦点待验','GET 历史；POST 采样文件并追加记录，不重做工具动作','G2-06','Episode 且有受支持 reconciliation')
surface('MAP-09','MAP','关系详情和来源事件','relationInspector','选择歧义／详情／缺失引用／来源读取／失败','原生 dialog；端点按钮；返回落点待实机','本地边记录；GET 引用事件','G2-04 G2-06 G1-05')
surface('MAP-10','MAP','悬空关系诊断','relationDiagnostics openDiagnostics','列表／分页／失败重试／记录详情／记录过期','详情标题；返回原 opener；dialog 键盘待实机','GET dangling 与 node，无修复写入','G2-04 G1-05')
surface('MAP-11','MAP','登记目标表单','goalCreateDialog newGoal','idle／busy／uncertain／success／404 可重填／校验失败','原生 modal autofocus；另一个目标聚焦 task；返回待验','POST 登记；GET by-task 可能同步落库回执','G2-05 G1-04 G1-05','非只读模板；切目标视图后入口可见；服务开关决定能力')
surface('MAP-12','MAP','笔记导出与 Obsidian 链接','exportNotes openVault openNote','无链接／已检测镜像／导出中／失败','原生链接与导出按钮','GET 生成 ZIP；外部 obsidian URI 打开；不主动改 vault','G2-04 G1-02')
surface('MAP-13','MAP','沉浸面板和退出','immersiveHud immersiveToggle','进入／退出／详情抽屉／局部抽屉／尺寸变化','Esc 优先级；焦点返回待实机','本地布局与相机范围','G1-04 G1-06 G2-04')
surface('LIST-01','LIST','记忆层与条目','memTiers memEntries','层读取／层选择／加载／空／失败','动态层按钮保留焦点；条目展示','GET tiers／entries','G2-07 G1-04')
surface('LIST-02','LIST','跨层搜索','memSearch memSearchBtn','编辑／搜索中／成功／空／失败','Enter 查询；原输入','POST 搜索读取，不按 HTTP 方法误判为写入','G2-07')
surface('MON-01','MON','旧监控卡片与刷新','refreshBtn entityFilter','轮询／空／失败／筛选／卡片折叠','卡片点击，键盘待验','多路 GET／WS；未在完整 UI 路由登记','G2-07 G1-04')
surface('MON-02','MON','旧监控对话','chatForm chatInput streamToggle sendBtn','提交／WS 或 SSE／回复／失败','输入与发送，模式无新选择入口','POST 聊天；旧客户端接线另验','G2-02 G2-03 G2-07')
surface('MVSC-01','MVSC','MVSC 阶段、健康与开关观察','','阶段事件／未启用／GET 数据／失败','仅主题按钮与静态观察；无 language 入口','GET state/ready/ablation/runtime；WS mvsc_phase','G2-07 G1-04')
surface('PROMPT-01','PROMPT','受限只读提示','','提示／返回图谱','原生链接','导航，不恢复完整服务能力','G2-07 G1-04')

BY_ID={s['surfaceId']:s for s in SURFACES}
MEMBERS={page:{member:s['surfaceId'] for s in SURFACES if s['page'] in (page,'*') for member in s['members']} for page in PAGES}

class Controls(HTMLParser):
    void={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self,page):
        super().__init__();self.page=page;self.stack=[];self.controls=[];self.assets=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs); parents=[v for _,v in self.stack];chain=[v.get('id') for v in parents]+[a.get('id')]
        if tag in ('script','link') and (a.get('src') or a.get('href')):
            self.assets.append(a.get('src') or a.get('href'))
        if tag in ('button','input','select','textarea','summary','canvas','a') or 'data-card' in a:
            surfaceId=next((MEMBERS[self.page][id] for id in reversed(chain) if id in MEMBERS[self.page]),None)
            classification='source-ancestry'
            if surfaceId is None and self.page=='SET' and a.get('data-i18n')=='studio.journal':
                surfaceId='SET-04';classification='journal-attribute'
            if surfaceId is None and self.page=='MON' and 'data-card' in a:
                surfaceId='MON-01';classification='card-fold-attribute'
            if surfaceId is None and tag=='a':
                surfaceId='COMMON-03';classification='navigation-link'
            if surfaceId is None and self.page=='CHAT':
                surfaceId='CHAT-02' if 'data-chat-mode' in a else PAGES[self.page][3]
                classification='mode-attribute' if 'data-chat-mode' in a else 'page-default'
            if surfaceId is None:classification='page-default'
            surfaceId=surfaceId or PAGES[self.page][3]
            self.controls.append(dict(surfaceId=surfaceId,tag=tag,id=a.get('id'),line=self.getpos()[0],
                attrs={k:v for k,v in a.items() if k in ('id','class','type','href','role','tabindex','maxlength','title','aria-label','aria-controls','aria-describedby','aria-haspopup') or k.startswith('data-')},
                hiddenInSource='hidden' in a or any('hidden' in v for v in parents),
                disabledInSource='disabled' in a,ancestorIds=[v for v in chain if v],
                classification=classification,renderingStatus='Jinja conditions not rendered or runtime-tested'))
        if tag not in self.void:self.stack.append((tag,a))
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break

def route_catalog():
    result=[]
    for path in sorted((ROOT/'app/api_routes').glob('*.py'))+[ROOT/'app/main.py']:
        rel=path.relative_to(ROOT).as_posix();tree=ast.parse(read(rel));prefixes={}
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call):
                for kw in node.value.keywords:
                    if kw.arg=='prefix' and isinstance(kw.value,ast.Constant):
                        for target in node.targets:
                            if isinstance(target,ast.Name):prefixes[target.id]=kw.value.value
        for node in ast.walk(tree):
            if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            for dec in node.decorator_list:
                if isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and dec.func.attr in ('get','post','put','delete','patch','websocket') and dec.args and isinstance(dec.args[0],ast.Constant):
                    router=dec.func.value.id if isinstance(dec.func.value,ast.Name) else ''
                    result.append(dict(method=dec.func.attr.upper(),path=prefixes.get(router,'')+dec.args[0].value,
                        handler=node.name,source=rel,line=dec.lineno,endLine=node.end_lineno))
    return result

APIS=[]
def api(id,method,path,surfaces,client,needle,effect,identity,outcomes,gate,tests):
    APIS.append(dict(apiId=id,method=method,path=path,surfaceIds=surfaces.split(),clientRef=ref('ui/web/'+client if client.startswith('templates/') else 'ui/web/static/'+client,needle),effect=effect,identity=identity,outcomes=outcomes,gate=gate,testFiles=tests.split()))

api('A01','GET','/api/chat/modes','CHAT-02','chat.js','/api/chat/modes','读取能力（可初始化 LLM adapter；不是提交任务）','mode','normal/deep 能力；失败有回退，真实能力待验','完整服务','tests/js/test_cube_chat.cjs tests/test_chat_modes.py')
api('A02','POST','/api/chat','CHAT-01 CHAT-03 MON-02','chat.js','async _sendViaWS','写入：接纳事件、执行任务；remember=true 可保留用户陈述','新 task_id/event_id；提交时 mode 固定','accepted true/false；不确认则 unknown，查询而非自动重发','runtime admission','tests/js/test_chat_receipts.cjs tests/test_request_receipts.py')
api('A03','POST','/api/chat/stream','CHAT-01 MON-02','chat.js','/api/chat/stream','写入：接纳任务并流式等待；断流不取消任务','X-EVA-Task-ID 与终态身份','SSE 终态／断流／unknown；迟到查询不能覆盖新任务','runtime admission','tests/js/test_chat_receipts.cjs tests/test_chat_modes.py')
api('A04','GET','/api/chat/result/{task_id}','CHAT-03','chat.js','/api/chat/result/','查询结果；不重复派发任务，registry.lookup 内部持久化行为需联调','原 task_id','202 执行中／200 终态／404 未知或过期／503 存储不可用','result retention','tests/js/test_chat_receipts.cjs tests/test_request_receipts.py')
api('A05','WEBSOCKET','/ws','CHAT-07 SET-06 MVSC-01 MON-01','chat.js','const url =','接收状态／chat_reply；通道连接与认证，非新任务提交','task_id；channel；4401/4403','连接／重连／迟到回执／登录更新','服务认证；不采集实际令牌','tests/js/test_chat_receipts.cjs tests/test_cloudflare_access.py')
api('A06','GET','/api/memory/graph','MAP-01 MAP-02','memory_graph.js',"'/api/memory/graph?'",'读取受限投影，不修改 DB/vault','q,tiers,limit<=600,edge_limit<=3000','200 范围／422 tiers 无效／503 来源不可用','memory source','tests/js/test_memory_graph.cjs tests/test_memory_graph.py')
api('A07','GET','/api/memory/graph/neighbors','MAP-04','memory_graph.js','/api/memory/graph/neighbors','读取邻居分页；客户端合并记录','tier,record_id,limit,offset；neighborVersion','200/404 过期/422/503；旧读取不覆盖新中心','memory source','tests/js/test_memory_graph_interactions.cjs tests/test_memory_graph.py')
api('A08','GET','/api/memory/graph/node','MAP-05 MAP-09 MAP-10','memory_graph.js','/api/memory/graph/node','读取单条记录；不补造缺失记录','tier,record_id；detailVersion','200/404 缺失/422/503；保留已显示范围','memory source','tests/js/test_memory_diagnostics.cjs tests/test_memory_graph.py')
api('A09','GET','/api/memory/graph/dangling','MAP-10','memory_diagnostics.js','/api/memory/graph/dangling','读取悬空关系诊断，不修复关系','limit,offset；列表读取代数','页／空／503／详情404','memory source','tests/js/test_memory_diagnostics.cjs tests/test_memory_graph.py')
api('A10','GET','/api/obsidian/export','MAP-12','memory_graph.js','/api/obsidian/export','生成 ZIP 供下载，不写 vault','limit<=600；button 防重复','ZIP／503；原生保存待验','memory source','tests/test_memory_graph.py')
api('A11','GET','/api/goals/graph','MAP-01 MAP-06','memory_graph.js','/api/goals/graph','读取已存目标投影，不检查文件','limit<=40,offset,q','200/503 功能未启用或存储失败','enable_business_goals','tests/js/test_memory_graph.cjs tests/test_business_goal_api.py')
api('A12','POST','/api/goals','MAP-11','goal_create.js',"'/api/goals'",'写入：登记目标并可能同步已收到回执；不执行任务或自动检查文件','已有 task_id,event_id；一任务绑定一个目标','201／404 回执缺失／409 绑定冲突／422／503；不自动重发','business_goals 与 accepting','tests/js/test_goal_create.cjs tests/test_business_goal_api.py')
api('A13','GET','/api/goals/by-task/{task_id}','MAP-11','goal_create.js','/api/goals/by-task/','状态同步：_reconcile 可 store.record_receipt 落库；不能标完全只读','boundTask；_reconcile 读取 task 回执','200／404 未登记／409／503；查询不会再次 create 或 verify','business_goals','tests/js/test_goal_create.cjs tests/test_business_goal_api.py')
api('A14','GET','/api/goals/{goal_id}/history','MAP-06','memory_graph.js',"'/history?'",'读取历史检查记录，不运行新检查','goal_id,limit<=50,offset；goalHistoryVersion','200／404／409／503；错误保留旧记录','business_goals','tests/js/test_memory_graph.cjs tests/test_business_goal_api.py')
api('A15','POST','/api/goals/{goal_id}/verify','MAP-06','memory_graph.js',"action==='verify'?'verify':'cancel'",'写入：指定文件核验与检查记录；先同步回执','goal_id,expected_version 严格整数','200／404／409 版本冲突／422／503；前端不推断超时结果','business_goals 与 accepting','tests/js/test_memory_graph.cjs tests/test_business_goal_api.py')
api('A16','POST','/api/goals/{goal_id}/cancel','MAP-06','memory_graph.js',"action==='verify'?'verify':'cancel'",'写入：取消目标跟踪；不取消已接纳聊天任务','goal_id,expected_version','200／404／409／422／503','business_goals','tests/js/test_memory_graph.cjs tests/test_business_goal_api.py')
api('A17','GET','/api/action-context/{task_id}/graph','MAP-07','memory_graph.js','/api/action-context/','读取已有输入／意图引用，不核验现内容','task_id,event_id；actionInputVersion','200／404／409 来源不一致／422／503','enable_action_context 依赖 enable_durable_requests','tests/js/test_memory_graph.cjs tests/test_action_context_graph.py')
api('A18','GET','/api/tool-observations/{action_id}','MAP-08','memory_graph.js','/api/tool-observations/','读取已存独立观测，不采样文件','action_id,event_id','200／409 binding conflict／422／503','enable_processing_episodes','tests/js/test_memory_graph.cjs tests/test_tool_observation_api.py')
api('A19','POST','/api/tool-observations/{action_id}','MAP-08','memory_graph.js','/api/tool-observations/','写入：服务端采样固定文件并追加观测；不重做原动作','action_id,event_id,expected_count<32','201／409 并发冲突／422／503；超时先读已有记录','processing_episodes 与 accepting','tests/js/test_memory_graph.cjs tests/test_tool_observation_api.py')
api('A20','GET','/api/runtime','CHAT-07 SET-06 MAP-02 MON-01 MVSC-01','runtime_status.js','/api/runtime','读取运行观察，非任务控制','AbortController；cache no-store','200／503 controller 缺失／登录更新','完整 runtime；预览未连接','tests/js/test_runtime_status.cjs tests/test_runtime_activity.py')
api('A21','GET','/api/memory/tiers','LIST-01 SET-06 MON-01','memory_explorer.js','/api/memory/tiers','读取层统计；format=explorer 用于列表','format；各层可用性','200／来源缺失分层说明；旧客户端失败待联调','memory','tests/test_memory_graph.py')
api('A22','GET','/api/memory/entries/{tier}','LIST-01','memory_explorer.js','/api/memory/entries/','读取按层条目','tier,limit,offset','200 页／空／错误；显示来源字段','memory','tests/js/test_memory_graph.cjs')
api('A23','POST','/api/memory/search','LIST-02','memory_explorer.js','/api/memory/search','读取：POST 搜索，不按 verb 判写入','query,tiers,limit','200 结果／空／验证错误；旧 fetch 认证待验','memory','tests/test_memory_graph.py')
for id,path,effect in [('A24','/api/memory/world','读取世界投影'),('A25','/api/policy/state','读取策略观察'),('A26','/health/diagnostic','读取健康诊断'),('A27','/health/ready','读取就绪状态'),('A28','/api/state','状态同步：更新 system_state 计数，非只读不可变快照'),('A29','/api/executors/audit/replay','读取审计时间线，不执行动作重放'),('A30','/api/agents','读取 Agent 清单'),('A31','/api/scheduler/jobs','读取已有调度项')]:
    api(id,'GET',path,'SET-06 MON-01','settings.js',path,effect,'查询参数或周期轮询','200／无数据／失败分支待实际服务核验','完整 container','tests/js/test_runtime_status.cjs')
api('A32','GET','/api/mvsc/ablation','MVSC-01','templates/mvsc_dashboard.html','/api/mvsc/ablation','读取功能开关，不调用 toggle POST','实验运行可用性','200／功能关闭或未装配返回分支待验','MVSC runtime attachment','tests/mvsc/test_e2e.py')

FINDINGS=[
    dict(id='G0-F01',priority='P0',kind='confirmed-contract-gap',summary='聊天 maxlength=32000，API ChatRequest.text max_length=4000；客户端没有同名 4000 限制。',refs=[ref('ui/web/templates/chat.html','maxlength="32000"'),ref('app/api_routes/routes_chat.py','max_length=4000')],tasks=['G2-02','G2-03','G4-01'],decision='在隔离联调验证 4000／4001／长草稿；后续修复需保持草稿及明确错误，不自动截断。'),
    dict(id='G0-F02',priority='P0',kind='confirmed-side-effect-contract',summary='GET goals/by-task 和相关 get_goal 会 _reconcile/record_receipt；GET api/state 更新内存计数。',refs=[ref('app/api_routes/routes_goals.py','def _reconcile'),ref('app/api_routes/routes_goals.py','def goal_for_task'),ref('app/api_routes/routes_chat.py','ss["pending_events"]')],tasks=['G2-05','G2-07'],decision='修正接口分类；同步已有回执不等于执行新检查。该行为本身不判为代码缺陷。'),
    dict(id='G0-F03',priority='P1',kind='confirmed-locale-coverage-gap',summary='MAP、目标创建、MVSC、受限提示没有共享 I18N 脚本和 langBtn；不能宣称全部子界面完整中英。',refs=[ref('ui/web/templates/memory_graph.html','goalCreateTitle'),ref('ui/web/templates/mvsc_dashboard.html'),ref('ui/web/templates/service_unavailable.html')],tasks=['G1-04','G2-04','G2-05','G2-07','G4-02'],decision='登记实际语言支持范围；子状态文案纳入统一语言改造或明确延期，现有主题接入不代表翻译完成。'),
    dict(id='G0-F04',priority='P1',kind='confirmed-route-scope',summary='dashboard.html 仅固定预览 /dashboard 路由；完整 routes_ui 默认 / 返回 index.html（星图）。',refs=[ref('scripts/preview_cube_chat.py','"/dashboard"'),ref('app/api_routes/routes_ui.py','def index')],tasks=['G2-07','G5-04'],decision='旧监控保留为待确认交付入口；不新增路由来补造已有产品能力。'),
    dict(id='G0-F05',priority='P0',kind='integration-risk-unverified',summary='新聊天／星图走 EvaHttp；settings、app、memory_explorer、MVSC 内联仍有直接 fetch。认证失效处理一致性未实测。',refs=[ref('ui/web/static/http.js','LoginRequired'),ref('ui/web/static/settings.js','const r = await fetch'),ref('ui/web/static/memory_explorer.js','await fetch')],tasks=['G2-03','G2-07','G4-01'],decision='隔离环境测试 401、登录页及 JSON 错误；源码差异不是已经复现的认证缺陷。'),
    dict(id='G0-F06',priority='P1',kind='keyboard-risk-unverified',summary='运行和旧监控 data-card 卡片标题绑定 click；当前模板该入口非原生 button，键盘折叠需实测。',refs=[ref('ui/web/static/settings.js','Card collapse'),ref('ui/web/templates/settings.html','data-card')],tasks=['G1-04','G4-02'],decision='登记键盘覆盖风险；不把静态检查当作屏幕阅读器实测。'),
]

def main():
    if (OUT/'artifact-manifest.json').exists():
        raise ValueError('Candidate evidence is sealed; create a new candidate instead of overwriting it')
    existing=OUT/'acceptance-index.json'
    if existing.exists():
        previous=json.loads(existing.read_text(encoding='utf-8'))
        if any(not t['taskId'].startswith('G0-') and t['status']!='planned' for t in previous['tasks']):
            raise ValueError('Later-stage work exists; create a new candidate instead of overwriting its evidence')
    began=datetime.now(timezone.utc).isoformat()
    spec=importlib.util.spec_from_file_location('release_capture',ROOT/'scripts/check_ui_release.py')
    release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
    before=release.source_snapshot();report=json.loads(read(REPORT));brand=json.loads(read('ui/web/static/brand-source.json'))
    extra=set()
    for directory in ['app','app/api_routes','runtime','memory','event','packages/contracts','packages/kernel']:
        extra.update(p.relative_to(ROOT).as_posix() for p in (ROOT/directory).glob('*.py'))
    extra.update(['core/chat_mode.py','core/llm_adapter.py','connectors/obsidian.py','scripts/preview_cube_chat.py'])
    apiHashes={p:sha(p) for p in sorted(extra)}
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    status=subprocess.run(['git','status','--porcelain=v1','--untracked-files=all','--','ui','app','runtime','memory','event','packages/contracts','packages/kernel','core/chat_mode.py','core/llm_adapter.py','connectors/obsidian.py','tests/js','scripts'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
    write('working-tree-status.txt',status)
    comparison={'unchanged':sorted(p for p,h in before.items() if report['sourceHashes'].get(p)==h),
        'changed':sorted(p for p,h in before.items() if p in report['sourceHashes'] and report['sourceHashes'][p]!=h),
        'added':sorted(set(before)-set(report['sourceHashes'])),'removed':sorted(set(report['sourceHashes'])-set(before))}
    comparison['exactMatch']=not any(comparison[k] for k in ('changed','added','removed'))
    packages=[]
    for name in ['artifacts/ui-release/local-20261008-config-review/eva-2x2-source.zip','artifacts/ui-release/local-20261008-config-review/eva-3x3-source.zip','artifacts/ui-delivery-followup/20261008/browser-order3-dark-front-512.zip']:
        if (ROOT/name).exists():
            result=release.verify_archive(ROOT/name,brand)
            packages.append(dict(path=name,sha256=sha(name),validation=result,scope='content verification; native save/offline execution not established'))
    baseline=dict(schemaVersion=1,candidateId=CANDIDATE,capturedAt=began,kind='working-tree-snapshot',gitHeadReference=head,
        gitHeadIsCandidate=False,versions={k:brand[k] for k in ['brandVersion','componentVersion','tokenSchemaVersion','formatVersion']},
        sourceHashes=before,apiAndDependencyHashes=apiHashes,scopeNote='API dependencies fingerprinted conservatively; not a full backend audit or test claim',
        sourceSetSha256=digest({'product':before,'apiDependencies':apiHashes}),captureScriptSha256=sha(str(Path(__file__).relative_to(ROOT)).replace('\\','/')),
        referenceReport={'path':REPORT,'sha256':sha(REPORT),'date':report['date'],'status':report['status'],'sourcesStable':report['sourcesStable'],'comparison':comparison},
        relatedApiTestStatus='not run for this capture; historical frontend report does not certify these API bytes',packages=packages,
        manualGates=report['unverifiedGates'],captureScope='source review only; no browser or service calls')
    write('source-baseline.json',baseline)
    controls=[];pages=[]
    for page,(template,route,controller,default) in PAGES.items():
        parser=Controls(page);parser.feed(read('ui/web/templates/'+template))
        for control in parser.controls:control.update(page=page,template='ui/web/templates/'+template)
        controls.extend(parser.controls)
        pages.append(dict(pageId=page,template=template,routeScope=route,controllers=controller,
            assetRefs=parser.assets,controlCount=len(parser.controls),defaultSurface=default,
            evidenceScope='source inventory only; actual rendering/keyboard/locale not re-tested'))
    dynamic=[('CHAT-03','chat.js','message-copy / retry / query / code-copy'),('MAP-01','memory_graph.js','nodeList buttons[data-node-id] / tier buttons'),('MAP-04','memory_graph.js','localBreadcrumbs / localPathSteps'),('MAP-05','memory_graph.js','neighbor / relation-open buttons'),('MAP-08','memory_graph.js','toolChecks sample / history'),('MAP-09','memory_graph.js','relationChoices'),('MAP-10','memory_diagnostics.js','diagnosticItems links'),('LIST-01','memory_explorer.js','buttons[data-tier]')]
    write('control-inventory.json',dict(schemaVersion=1,candidateId=CANDIDATE,pages=pages,sourceControls=controls,
        dynamicControlFamilies=[dict(surfaceId=s,source='ui/web/static/'+f,controls=c) for s,f,c in dynamic],
        note='Includes native controls, canvas and data-card click entries. HTML source/Jinja is not rendered UI; generated controls are explicit families, not counted individual instances.'))
    surfacesDoc=['# 界面与子状态清单',f'候选：`{CANDIDATE}`。七类界面，{len(SURFACES)} 个逻辑区域，{len(controls)} 个源码控件入口及 {len(dynamic)} 类动态控件。来源为源码审阅，不是新一轮浏览器验收。','',
        '模板入口与所有源码控件详见 [control-inventory.json](control-inventory.json)。每个控件已归到区域；动态按钮单列。通用导航有独立归类，Jinja 条件未在此轮渲染。','',
        '| 页面 | 实际路由范围 | 模板／控制器 |','| --- | --- | --- |']
    for p in pages:surfacesDoc.append(f"| {p['pageId']} | {p['routeScope']} | {p['template']}；{p['controllers']} |")
    surfacesDoc += ['', '## 区域、状态与操作影响','| surfaceId／入口 | 状态 | 焦点／键盘 | 数据与操作影响 | 可用条件 | 后续任务 |','| --- | --- | --- | --- | --- | --- |']
    for s in SURFACES:surfacesDoc.append(f"| {s['surfaceId']} · {s['title']} | {s['states']} | {s['focus']} | {s['effect']} | {s['gate']} | {', '.join(s['tasks'])} |")
    surfacesDoc += ['', '## 已有参考与当前缺口',
        '- CHAT／SET／LIST 接入共享语言；MAP、目标表单、MVSC、提示页没有同等语言切换入口。主题接入和翻译覆盖分开。',
        '- 既有七页主题／布局、对话阅读、星图显示面板、设置导航和审阅证据见 [适配记录](../../../docs/ui-openai-adaptation.md)与[交互记录](../../../docs/ui-interaction-upgrade.md)。这些是历史参考，未代表本轮逐区域运行通过。',
        '- 目标、ACT 历史输入、工具观测、诊断、沉浸及开关关闭分支需 G2 子状态联调；真实缩放、辅助技术与手机输入需 G1。',
        '- 各区域共同读取 `ui-system.css`／`ui-theme.js`；主题 Token 不改变 Logo 的主几何。键盘文字列记源码行为或明确待验，不作为实际播报证据。',
        '- 来源模式：完整服务、合成预览、真实数据库只读服务、便携包分别登记；只读服务禁用目标并把对话／设置转到提示模板。',
        '- 便携 `preview.html` 是品牌包派生产物，归属 SET-02／SET-05 的独立调用端，G1-03／G5-02 验证，不把它计为第八个产品页面。', '']
    write('surface-inventory.md','\n'.join(surfacesDoc))
    write('surfaces.json',dict(candidateId=CANDIDATE,surfaces=SURFACES))
    catalog=route_catalog();write('route-catalog.json',catalog)
    for a in APIS:
        matches=[r for r in catalog if r['method']==a['method'] and r['path']==a['path']]
        if len(matches)!=1:raise ValueError(f"API route must resolve once: {a['method']} {a['path']} => {matches}")
        a['serverRef']=matches[0]
        for f in a['testFiles']:
            if not (ROOT/f).is_file():raise ValueError('Missing related test '+f)
    write('api-map.json',dict(candidateId=CANDIDATE,apis=APIS,relatedSourceHashKey='source-baseline.json.apiAndDependencyHashes',
        note='32 client-bound contracts plus related route catalog. GET can synchronize state; POST search can be read-only. Tests named are relevant existing files, not newly run evidence.'))
    apiDoc=['# UI 与 API 依赖映射',f'候选 `{CANDIDATE}`；{len(APIS)} 项客户端接口映射，均已对照实际路由装饰器及前端调用位置。此轮没有请求服务。','',
        '## 请求、身份与副作用','| 编号／区域 | 方法／路径 | 操作影响 | 身份与竞争字段 | 结果／错误 | 开关／依赖 |','| --- | --- | --- | --- | --- | --- |']
    for a in APIS:apiDoc.append(f"| {a['apiId']} · {','.join(a['surfaceIds'])} | {a['method']} `{a['path']}` | {a['effect']} | {a['identity']} | {a['outcomes']} | {a['gate']} |")
    apiDoc+=['','## 源码与关联测试','| 编号 | 客户端 | 服务端处理函数 | 关联测试文件（本轮未运行） |','| --- | --- | --- | --- |']
    for a in APIS:apiDoc.append(f"| {a['apiId']} | {mdref(a['clientRef'])} | {mdref({'path':a['serverRef']['source'],'line':a['serverRef']['line']})} · {a['serverRef']['handler']} | {', '.join(a['testFiles'])} |")
    apiDoc+=['','## 非业务接口与变更边界',
        '- 品牌选择、审阅、取消、应用／撤销、主题与语言仅管理本地呈现偏好。品牌导出读取 `/static/brand-source.json` 与批准组件文件，写入浏览器下载，不导入服务参数或凭据。',
        '- 剪贴板复制是显式本地能力；`obsidian://` 为外部应用打开。图谱 GET 导出只生成档案，不主动同步或覆盖笔记库。',
        '- G0-F02：目标查询同步回执；G2 验收约束应为不派发新任务／不新做文件检查，而非假设零持久化写入。目标 cancel 也不等于取消聊天执行。',
        '- API 开关读取源码默认关闭；本轮没有读取实际环境变量或启动状态，不将源码默认值当作当前运行配置。',
        '- 产品调用端：chat＋cube-avatar；brand-system＋brand-config＋brand-package；memory_graph＋goal_create＋memory_diagnostics；settings/app/MVSC＋runtime；memory_explorer。共享呈现变更需覆盖相关调用端。',
        '- 关联后端文件：聊天／admission／结果 registry；memory graph projection／neighbors；business goals／Episode／action context；runtime observation。依赖指纹已在基线中单列，不能由 UI 的 34 项 Python 报告概括。',
        '- [working-tree-status.txt](working-tree-status.txt)记录相关路径的真实修改／未跟踪状态。候选为工作树字节快照，Git HEAD 仅参考；没有提交、暂存、重置或清理文件。',
        '- 完整路由目录是源码索引，不代表所有管理接口都有 UI 入口或纳入本次联调；本表仅列当前客户端调用契约。', '']
    write('api-map.md','\n'.join(apiDoc))
    write('findings.json',dict(candidateId=CANDIDATE,findings=FINDINGS,status='source findings; browser/service reproduction explicitly scoped per item'))
    findingsDoc=['# G0 发现与后续处置','源码问题与未验证风险分别登记；不是生产缺陷复现报告。','',
        '| 编号 | 优先级／证据等级 | 发现 | 后续 | 处置 |','| --- | --- | --- | --- | --- |']
    for f in FINDINGS:findingsDoc.append(f"| {f['id']} | {f['priority']} · {f['kind']} | {f['summary']} | {', '.join(f['tasks'])} | {f['decision']} |")
    findingsDoc+=['','## 源码依据']
    for f in FINDINGS:findingsDoc.append(f"- {f['id']}："+'；'.join(mdref(r) for r in f['refs']))
    write('findings.md','\n'.join(findingsDoc)+'\n')
    taskLines=[l for l in read(PLAN).splitlines() if re.match(r'^\| G[0-5]-\d{2} ·',l)]
    tasks=[]
    for l in taskLines:
        cells=[v.strip() for v in l.strip('|').split('|')]
        id,priority,title=cells[0].split(' · ')
        deps=re.findall(r'G[0-5]-\d{2}',cells[4])
        if id=='G3-05':deps=['G3-02','G3-03','G3-04']
        h=re.search(r'(\d+)–(\d+)h',cells[4])
        tasks.append(dict(taskId=id,title=title,priority=priority,status='passed' if id.startswith('G0-') else 'planned',
            startedAt=None,startTimeRecorded=False,completedAt=None,
            dependencyText=cells[4].split('；')[0],dependsOnTasks=deps,dependenciesNeedExternalState='前置 P0/环境/实机/开关按 dependencyText 单独判断，不伪造通过',
            scope=cells[1],deliverable=cells[2],acceptanceCriteria=cells[3],estimateHours=[int(h[1]),int(h[2])],
            environmentNeeds='源码与现有报告' if id.startswith('G0-') else '对应任务定义的原生设备／隔离服务／稳定候选，尚未核验',
            evidencePaths={'G0-01':['source-baseline.json','working-tree-status.txt'],'G0-02':['surface-inventory.md','control-inventory.json','surfaces.json'],'G0-03':['api-map.md','api-map.json','route-catalog.json','findings.md'],'G0-04':['README.md','acceptance-index.json','g0-validation.json']}.get(id,[]),
            referenceEvidence=[REPORT] if id.startswith('G0-') else [],
            findings=[f['id'] for f in FINDINGS if id in f['tasks']],
            note='源码审阅和产物一致性通过；不表示 G1–G5 通过' if id.startswith('G0-') else '未执行，不引用历史材料冒充本候选结果'))
    taskIds={t['taskId'] for t in tasks}
    assert len(taskIds)==len(tasks)==30
    assert all(set(t['dependsOnTasks'])<=taskIds for t in tasks)
    assert all(c['surfaceId'] in BY_ID for c in controls)
    assert all(set(a['surfaceIds'])<=set(BY_ID) for a in APIS)
    stable=before==release.source_snapshot() and apiHashes=={p:sha(p) for p in apiHashes}
    assert stable, 'Source changed during capture; recapture before marking G0 passed'
    validation=dict(candidateId=CANDIDATE,finishedAt=datetime.now(timezone.utc).isoformat(),captureBeganAt=began,
        scope='G0 source review and artifact consistency; no new frontend regression/browser/service run',
        reportSourceExactMatch=comparison['exactMatch'],productSourceFiles=len(before),apiDependencyFiles=len(apiHashes),
        sourcesStableDuringCapture=stable,pageClasses=len(PAGES),surfaceAreas=len(SURFACES),sourceControlEntries=len(controls),
        dynamicControlFamilies=len(dynamic),unassignedControls=0,clientApiContracts=len(APIS),allApiRoutesResolved=True,
        allReferencedTestFilesExist=True,taskCount=len(tasks),taskStatusCounts=dict(Counter(t['status'] for t in tasks)),
        findings=len(FINDINGS),estimatedHours=[sum(t['estimateHours'][i] for t in tasks) for i in (0,1)],
        g0Passed=True,overallReleasePassed=False,manualGateStatus='unverified')
    write('g0-validation.json',validation)
    for t in tasks:
        if t['taskId'].startswith('G0-'):t['completedAt']=validation['finishedAt']
    write('acceptance-index.json',dict(schemaVersion=1,candidateId=CANDIDATE,candidateSourceSetSha256=baseline['sourceSetSha256'],
        generatedAt=validation['finishedAt'],scope='30 task working sheet; first four source-review tasks passed only',
        overallStatus='G0-passed; G1-G5-not-executed; release-unverified',tasks=tasks,
        referenceEvidenceSeparated=True,supportScope={'nativeBrowsers':'Chrome/Edge requested; current connection not checked','screenReader':'not checked','phone':'not checked','languages':'partial Chinese/English; see G0-F03'},
        manualGates=report['unverifiedGates'],nextTaskIds=['G1-01','G2-01']))
    write('README.md',f'''# UI 候选验收入口

候选 `{CANDIDATE}`；工作树快照，Brand {brand['brandVersion']}／Components {brand['componentVersion']}／Token schema {brand['tokenSchemaVersion']}／Package format {brand['formatVersion']}。

G0-01–04 已完成源码审阅与产物一致性检查。G1–G5 的 26 项仍待执行，整体发布未通过。本轮没有运行浏览器、启动服务、请求 API、读取业务数据库或实际凭据，也没有提交／清理 Git。

| 产物 | 用途 |
| --- | --- |
| [source-baseline.json](source-baseline.json) | 当前源码与关联 API 指纹、版本、历史报告差异与包内容核对 |
| [surface-inventory.md](surface-inventory.md) | 七类界面、{len(SURFACES)} 个逻辑区域及其状态、操作影响和待验收范围 |
| [control-inventory.json](control-inventory.json) | {len(controls)} 个源码控件归类与 {len(dynamic)} 类动态控件；不是运行时实例数 |
| [api-map.md](api-map.md) | {len(APIS)} 项 UI 请求的实际服务路由、身份、副作用与关联测试 |
| [findings.md](findings.md) | {len(FINDINGS)} 项源码差异／待联调风险及后续任务 |
| [acceptance-index.json](acceptance-index.json) | 30 项工作单：4 passed、26 planned；依赖、验收、证据和发现关联 |
| [g0-validation.json](g0-validation.json) | 本轮指纹稳定、路由／控件／任务映射与数量核验 |
| [artifact-manifest.json](artifact-manifest.json) | 最终复核生成的文件哈希清单，固定本轮证据字节 |
| [working-tree-status.txt](working-tree-status.txt) | 相关路径修改状态，不代表已形成独立提交 |

历史完整报告为 [{REPORT}](../../../{REPORT})，其 475 项 JavaScript／34 项 Python、生成资产与双阶源码包是已存在的自动检查。本轮只核对当前指纹与该报告关系及包内容，不冒充重新跑过全部测试。关联 API 字节单独采集，尚未由本轮服务测试验证。

发现优先顺序：G0-F01 输入长度契约 → G0-F05 认证／错误联调 → G0-F02 查询副作用边界 → G0-F03 语言子区域 → G0-F04 旧监控路由范围 → G0-F06 卡片键盘。F02 属于必须准确登记的既有契约，并不自动要求更改后端。

下一批：G1-01 核验原生环境；G2-01 准备隔离联调。环境缺失保持未验证，继续可执行项。真实保存／离线、前台双阶长测、原生缩放、屏幕阅读器与手机软键盘仍无本候选通过证据。

支持范围由实际结果确定：目前只有历史内置浏览器与自动证据；中文／英文在 CHAT／SET／LIST 已接入，MAP／MVSC／PROMPT 语言覆盖仍有缺口。合成预览、完整服务、真实数据库只读服务、便携包分别验收。

状态词：planned 待执行；running 进行中；passed 验收通过；failed 已执行但失败；unverified 环境缺失；deferred 有明确影响说明的 P1 延期。证据引用不升级任务状态。

候选源码改变后建立新快照／候选关联，不能覆盖既有结果来伪造同版验收。此目录的 capture_g0.py 是 G0 采集方法，本次结果已保存；生成哈希清单后拒绝重新覆盖。后续执行在任务子目录记录，维护工作单时保留本轮清单与状态变更来源。
''')
    print(json.dumps(validation,ensure_ascii=False))

if __name__=='__main__':main()
