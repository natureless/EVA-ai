"""Append G1/G2 results without replacing the sealed G0 acceptance work sheet."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def main():
    sealed = read(OUT/'artifact-manifest.json')
    assert all(sha(OUT/name)==expected for name,expected in sealed['artifactHashes'].items())
    baseline = read(OUT/'source-baseline.json')
    for group in ('sourceHashes','apiAndDependencyHashes'):
        assert all(sha(ROOT/name)==expected for name,expected in baseline[group].items())
    reports = [read(path) for path in (OUT/'G2-01').glob('run-*/readiness.json')]
    passed = [r for r in reports if r['status']=='passed']
    assert passed, 'No passed readiness record'
    current = max(passed,key=lambda r:r['finishedAt'])
    environment = read(Path(current['environmentPath']))
    harness = OUT/'G2-01/run_isolated_service.py'
    assert environment['harnessSha256'] == sha(harness)
    assert len(current['checks']) == 18 and all(c['passed'] for c in current['checks'])
    assert not current['blockedAuditEvents']
    now = datetime.now(timezone.utc).isoformat()
    native = dict(taskId='G1-01', candidateId=OUT.name, status='unverified',
        recordedAt=now, startedAt=None, startTimeRecorded=False,
        browserConnector={'outcome':'timeout', 'error':'cua.getState call timed out after 300 seconds; no inventory returned'},
        windowsComputerUse={'runtime':'@oai/sky through node_repl',
            'browserWindowsReturned':{'Chrome':1,'MSEdge':1},
            'target':'Chrome window selected from returned list',
            'observationOutcome':'blocked before page accessibility was returned',
            'exactError':'Computer Use has been stopped for this turn because it could not determine the current browser URL on Windows with enough confidence to enforce policy. Stop your work and send a final message noting why Computer Use ended.',
            'subsequentWindowsInputCalls':0},
        observed={'browserVersions':None,'currentUrl':None,'pageOperable':False,
                  'nativeZoom':None,'systemReducedMotion':None,'screenReader':None,'phone':None},
        limitation='Tool policy check could not identify URL; this is not a product page failure or a confirmed browser disconnection.',
        noNativeSaveOfflineZoomOrPerformanceAcceptance=True)
    (OUT/'G1-01').mkdir(exist_ok=True)
    write(OUT/'G1-01/environment-check.json',native)
    # Preserve the first import failure; no start time or old helper hash was recorded.
    first = OUT/'G2-01/run-9y5sidcr'
    if first.is_dir() and not (first/'startup-failure.json').exists():
        write(first/'startup-failure.json',dict(status='failed',phase='app_import',
            startedAt=None,recordedAt=now,sourceHashRecorded=False,
            type='RuntimeError',message='Could not determine home directory.',
            provenance='Retrospective transcription of exec_command stderr; helper cleared USERPROFILE before Path.home import.'))
    attempts=[]
    for folder in sorted((OUT/'G2-01').glob('run-*')):
        report_path=next((p for p in (folder/'readiness.json',folder/'startup-failure.json') if p.exists()),None)
        if report_path:
            report=read(report_path)
            attempts.append({'folder':folder.name,'status':report['status'],
                'report':report_path.relative_to(OUT).as_posix(),
                'failure':report.get('failure') or ({'type':report.get('type'),'message':report.get('message')} if report['status']=='failed' else None),
                'classification':'harness preparation; no product defect inferred'})
    write(OUT/'G2-01/attempts.json',dict(currentPassedReport=str(Path(environment['sandbox'])/'readiness.json'),attempts=attempts))
    index = read(OUT/'acceptance-index.json')
    for task in index['tasks']:
        if task['taskId']=='G1-01':
            task.update(status='unverified',completedAt=None,
                evidencePaths=['G1-01/environment-check.json'],
                note=native['limitation'])
        if task['taskId']=='G2-01':
            task.update(status='passed',startedAt=current['startedAt'],startTimeRecorded=True,
                completedAt=current['finishedAt'],
                evidencePaths=['G2-01/README.md','G2-01/attempts.json',
                    Path(current['environmentPath']).relative_to(OUT).as_posix(),
                    (Path(current['environmentPath']).parent/'readiness.json').relative_to(OUT).as_posix()],
                environmentNeeds='Fresh per-run sandbox; actual app.main/SQLite/API routes; MockLLM; TestClient',
                note='Readiness only; no end-to-end browser or full G2-02 acceptance. Feature-on profiles require separate preparation.')
    index.update(generatedAt=now,scope='Current task status overlay; sealed G0 acceptance-index.json remains unchanged',
        previousWorkSheet='acceptance-index.json',overallStatus='G0-passed; G2-01-readiness-passed; native-unverified; release-unverified',
        nextTaskIds=['G2-02','G2-03'],
        statusCounts={'passed':5,'unverified':1,'planned':24})
    index['supportScope']['nativeBrowsers']='Native Windows windows enumerated; Chrome page capture policy stopped because URL could not be established. Versions and operability unverified.'
    assert dict(Counter(task['status'] for task in index['tasks']))==index['statusCounts']
    write(OUT/'progress.json',index)
    with (OUT/'status-history.jsonl').open('a',encoding='utf-8') as stream:
        for task,status,evidence in [('G1-01','unverified','G1-01/environment-check.json'),('G2-01','passed','G2-01/attempts.json')]:
            stream.write(json.dumps(dict(recordedAt=now,taskId=task,status=status,evidence=evidence),ensure_ascii=False)+'\n')
    sandbox_name=Path(environment['sandbox']).name
    (OUT/'G2-01/README.md').write_text(f'''# G2-01 隔离联调准备

状态：通过准备检查；18 项实际 API／启动检查通过。候选 `{OUT.name}`，界面与关联 API 字节仍匹配 G0 基线。

当前证据：[环境]({sandbox_name}/environment.json)、[准备检查]({sandbox_name}/readiness.json)、[准备过程与失败记录](attempts.json)。此前失败来自联调脚本自身：未设置隔离用户目录、局部类型注解被解释为查询参数、SQLite 只读 URI 未正确解析。分别修正后重新创建测试库，原失败记录保留，不能据此判产品图谱故障。

服务采用真实 `app.main`、认证、运行时、SQLite、模板、聊天接纳／回执和 WebSocket；模型固定 MockLLM。UI 为当前源码的字节一致副本，非固定预览回复服务器。每次运行生成新 `run-*` 目录，数据／日志／快照／配置写入该目录；不读取实际 .env、不继承 EVA／模型／代理参数、不复制生产数据库。仅允许回环网络，Python 审计约束不等同于操作系统沙箱。

本轮实际检查：缺凭据返回 401；使用本地测试令牌后运行时启动、6 个实际模板返回 HTML；正常／深度能力均为 mock；空库图谱可读；目标和 ACT 未启用返回 503；一条 normal 请求经过实际流水线并可查询同一回执；4001 字符被实际 API 返回 422；WebSocket ping 返回 pong；容器数据库路径位于隔离目录。运行时 `ready` 为真实组件结果，`llm=false` 与启动诊断 degraded 原样记录，不描述为真实模型健康。

运行方法（在 `D:\\EVA-ai`）：

```powershell
python artifacts/ui-acceptance/{OUT.name}/G2-01/run_isolated_service.py
# 可选：单独运行回环服务，使用新的隔离目录，Ctrl+C 结束
python artifacts/ui-acceptance/{OUT.name}/G2-01/run_isolated_service.py --serve --port 8784
```

`--serve` 使用实际认证中间件；HTTP／WS 客户端需发送 `X-API-Token: ui-acceptance-local-fixture-token`。这是公开的本地测试夹具，不是用户凭据。此轮通过记录来自 TestClient，未运行 TCP serve 模式或浏览器页面操作，不能用这些检查代替原生 UI 结果。

所有可选业务开关为 false，实际值与关闭分支已记录；目标／Episode／durable／ACT 开启状态的专门数据与环境仍需 G2-05／06 前准备。本轮未验证 Cloudflare 登录、远端模型、双模式完整 WS／SSE 交互或魔方终态，这些归后续任务。

下一批：G2-02 四组请求与固定模式；G2-03 不确定结果与竞争回执。原生 G1-01 的工具阻断见 [环境记录](../G1-01/environment-check.json)，不重复调用已中止的 Windows 操作。
''',encoding='utf-8')
    (OUT/'CURRENT.md').write_text('''# 当前验收状态

G0 四项和 G2-01 隔离联调准备通过；G1-01 因 Windows 工具无法可靠识别浏览器当前 URL 而未验证；另外 24 项待执行，整体验收与发布未通过。

- [当前 30 项工作单](progress.json)
- [G2-01 隔离环境与真实 API 准备证据](G2-01/README.md)
- [G1-01 原生环境阻断](G1-01/environment-check.json)
- [G0 原始证据入口](README.md)与[封存哈希](artifact-manifest.json)
- [状态变更记录](status-history.jsonl)

原始 G0 工作单及封存文件保持原字节。当前状态使用 progress.json 覆盖视图，不将历史入口里的 4 passed／26 planned 计数当作最新执行结果。其余任务没有借用这次准备检查标为通过。
''',encoding='utf-8')
    evidence=[OUT/'CURRENT.md',OUT/'progress.json',OUT/'status-history.jsonl',
        OUT/'G1-01/environment-check.json',OUT/'G2-01/README.md',OUT/'G2-01/attempts.json',harness,
        Path(current['environmentPath']),Path(current['environmentPath']).parent/'readiness.json']
    assert all(path.exists() for path in evidence)
    assert all(sha(OUT/name)==expected for name,expected in sealed['artifactHashes'].items())
    result=dict(recordedAt=now, sourceHashesUnchanged=True, sealedG0Unchanged=True,
        taskCount=30,statusCounts=index['statusCounts'],
        evidenceHashes={path.relative_to(OUT).as_posix():sha(path) for path in evidence})
    write(OUT/'G2-01/progress-validation.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='evidenceHashes'},ensure_ascii=False))

if __name__=='__main__':
    main()
