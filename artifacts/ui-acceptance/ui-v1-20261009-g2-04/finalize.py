"""Create a new candidate without overwriting prior source/evidence seals."""
import copy
import difflib
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PREVIOUS=ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g2-02'
REPORT=ROOT/'artifacts/ui-release/local-20261009-memory-status/report.json'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    if (OUT/'artifact-manifest.json').exists():raise ValueError('Candidate sealed; do not overwrite')
    old=read(PREVIOUS/'source-baseline.json');report=read(REPORT)
    spec=importlib.util.spec_from_file_location('release_checker',ROOT/'scripts/check_ui_release.py')
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    current=checker.source_snapshot()
    assert current==report['sourceHashes'] and report['sourcesStable']
    assert report['status']=='local-checks-passed; manual-gates-unverified'
    for folder in [PREVIOUS,ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g0-01']:
        assert all(sha(folder/name)==digest for name,digest in read(folder/'artifact-manifest.json')['artifactHashes'].items())
    api_hashes={name:sha(ROOT/name) for name in old['apiAndDependencyHashes']}
    assert api_hashes==old['apiAndDependencyHashes']
    changed=sorted(name for name,h in current.items() if name in old['sourceHashes'] and h!=old['sourceHashes'][name])
    added=sorted(set(current)-set(old['sourceHashes']))
    assert changed==['ui/web/static/memory_explorer.js','ui/web/templates/memory.html']
    assert added==['tests/js/test_memory_explorer.cjs'] and not set(old['sourceHashes'])-set(current)
    patch=[]
    for name in changed:
        before=OUT/'before'/Path(name).name
        assert sha(before)==old['sourceHashes'][name]
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(keepends=True),(ROOT/name).read_text(encoding='utf-8').splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name))
    patch.extend(difflib.unified_diff([],(ROOT/added[0]).read_text(encoding='utf-8').splitlines(keepends=True),fromfile='/dev/null',tofile='b/'+added[0]))
    (OUT/'implementation.patch').write_text(''.join(patch),encoding='utf-8')
    api_path=OUT/'run-0r8k8u6o/memory-api.json'
    api=read(api_path);replay=read(OUT/'client-replay.json')
    assert api['status']==replay['status']=='passed'
    assert len(api['checks'])==31 and len(replay['checks'])==17
    assert all(x['passed'] for x in api['checks']+replay['checks']) and not api['blockedAuditEvents']
    now=datetime.now(timezone.utc).isoformat()
    combined=hashlib.sha256(json.dumps({'product':current,'apiDependencies':api_hashes},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    write('source-baseline.json',dict(candidateId=OUT.name,capturedAt=now,kind='working-tree-snapshot',previousCandidate=PREVIOUS.name,
        previousBaselineSha256=sha(PREVIOUS/'source-baseline.json'),sourceHashes=current,apiAndDependencyHashes=api_hashes,sourceSetSha256=combined,
        versions=old['versions'],delta={'changed':changed,'added':added,'removed':[]},referenceReport={'path':REPORT.relative_to(ROOT).as_posix(),'sha256':sha(REPORT),'matchesCurrentSources':True},
        scope='Memory list request ownership/status/localization; no cube/exported brand component or API changes',manualGates=report['unverifiedGates']))
    progress=copy.deepcopy(read(PREVIOUS/'progress.json'))
    evidence=[api_path.relative_to(ROOT).as_posix(),(OUT/'client-replay.json').relative_to(ROOT).as_posix(),REPORT.relative_to(ROOT).as_posix()]
    for task in progress['tasks']:
        if task['taskId'] in ('G2-04','G2-07'):
            task.update(status='running',startedAt=api['startedAt'],completedAt=None,startTimeRecorded=True,evidencePaths=evidence,
                note='Graph/list/runtime automated scope passed; native interactions and remaining system surfaces require further verification.')
        if task['taskId'] in ('G4-01','G4-04'):
            task['evidencePaths']+=evidence
            task['note']='Input repair retained; new list race/error repair and relevant regressions passed. Remaining defects/manual validation still pending.'
    progress.update(candidateId=OUT.name,candidateSourceSetSha256=combined,generatedAt=now,previousWorkSheet=(PREVIOUS/'progress.json').relative_to(ROOT).as_posix(),
        overallStatus='Local automated graph/list/runtime scope passed; remaining integration/native/release not certified',
        nextTaskIds=['G2-07','G2-05','G2-06'],statusCounts=dict(Counter(t['status'] for t in progress['tasks'])))
    write('progress.json',progress)
    write('verification.json',dict(candidateId=OUT.name,sourceSetSha256=combined,generatedAt=now,status='local-checks-passed; manual-gates-unverified',
        javascriptPassed=489,uiPythonPassed=34,addedListRegressions=9,apiChecksPassed=31,clientReplayChecksPassed=17,
        releaseReport=REPORT.relative_to(ROOT).as_posix(),apiEvidence=api_path.relative_to(ROOT).as_posix(),replayEvidence=(OUT/'client-replay.json').relative_to(ROOT).as_posix(),
        sourceStable=report['sourcesStable'],releasePassed=False,manualGates=report['unverifiedGates']))
    write('findings-update.json',dict(candidateId=OUT.name,findings=[
        {'id':'G2-LIST-01','priority':'P1','status':'fixed-automatically-verified','problem':'Old browse/search responses replaced the current selection','regression':'late selection/late error/search versus browse'},
        {'id':'G2-LIST-02','priority':'P1','status':'fixed-automatically-verified','problem':'HTTP and login errors parsed as data; search failures could look empty; overview refresh lost valid cards','regression':'503/malformed/login/timeout/retry/stale overview'},
        {'id':'G0-F03','status':'partially-improved','note':'List state/title/cards translated; other graph/goal/MVSC surfaces still need shared language coverage'},
        {'id':'G0-F05','status':'partially-improved','note':'List now uses EvaHttp; settings and other remaining direct-fetch surfaces are not certified'}]))
    paths=[p for p in OUT.iterdir() if p.is_file() and p.name!='artifact-manifest.json']
    paths += [api_path,*sorted((OUT/'before').iterdir())]
    write('artifact-manifest.json',dict(candidateId=OUT.name,sealedAt=now,sourceSetSha256=combined,
        artifactHashes={p.relative_to(OUT).as_posix():sha(p) for p in paths},
        externalEvidenceHashes={REPORT.relative_to(ROOT).as_posix():sha(REPORT)},releasePassed=False))
    print(json.dumps({'candidate':OUT.name,'sources':len(current),'changed':changed,'added':added,'statusCounts':progress['statusCounts']},ensure_ascii=False))
if __name__=='__main__':main()
