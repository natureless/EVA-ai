"""Bind this batch's source, API replay and regression evidence to a new candidate."""
import copy
import difflib
import hashlib
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
PREVIOUS=ROOT/'artifacts/ui-acceptance/ui-v1-20261008-g0-01'
REPORT=ROOT/'artifacts/ui-release/local-20261008-chat-input/report.json'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    if (OUT/'artifact-manifest.json').exists():raise ValueError('Candidate sealed; do not overwrite evidence')
    old=read(PREVIOUS/'source-baseline.json');report=read(REPORT)
    spec=importlib.util.spec_from_file_location('release_checker',ROOT/'scripts/check_ui_release.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    current=module.source_snapshot()
    assert current==report['sourceHashes'] and report['sourcesStable']
    assert report['status']=='local-checks-passed; manual-gates-unverified'
    api_hashes={name:sha(ROOT/name) for name in old['apiAndDependencyHashes']}
    assert api_hashes==old['apiAndDependencyHashes']
    sealed=read(PREVIOUS/'artifact-manifest.json')
    assert all(sha(PREVIOUS/name)==expected for name,expected in sealed['artifactHashes'].items())
    api_path=OUT/'run-313w0url/api-contracts.json';api=read(api_path)
    replay=read(OUT/'client-replay.json')
    assert api['status']==replay['status']=='passed' and len(api['checks'])==33 and len(api['cases'])==len(replay['cases'])==4
    assert all(c['passed'] for c in api['checks']) and not api['blockedAuditEvents']
    for case in replay['cases']:
        assert case['finalActiveSolved'] and case['nextSolved'] and case['draftPreserved']
        assert case['initial']==case['finalActiveSnapshot']
    tests_path=OUT/'run-_8bsm0mu/backend-tests.json';tests=read(tests_path)
    assert tests['exitCode']==0 and not tests['blockedAuditEvents']
    readiness_path=max((OUT/'G2-01').glob('run-*/readiness.json'),key=lambda p:p.stat().st_mtime)
    readiness=read(readiness_path)
    assert readiness['status']=='passed' and len(readiness['checks'])==18
    before_files=old['sourceHashes']
    changed=sorted(p for p,h in current.items() if p in before_files and before_files[p]!=h)
    added=sorted(set(current)-set(before_files))
    removed=sorted(set(before_files)-set(current))
    assert changed==['ui/web/static/chat.js','ui/web/static/cube-chat.css','ui/web/templates/chat.html']
    assert added==['tests/js/test_chat_input.cjs'] and not removed
    now=datetime.now(timezone.utc).isoformat()
    combined=hashlib.sha256(json.dumps({'product':current,'apiDependencies':api_hashes},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    write('source-baseline.json',dict(candidateId=OUT.name,capturedAt=now,kind='working-tree-snapshot',
        previousCandidate=PREVIOUS.name,previousBaselineSha256=sha(PREVIOUS/'source-baseline.json'),
        sourceHashes=current,apiAndDependencyHashes=api_hashes,sourceSetSha256=combined,
        versions=old['versions'],delta={'changed':changed,'added':added,'removed':removed},
        referenceReport={'path':REPORT.relative_to(ROOT).as_posix(),'sha256':sha(REPORT),'matchesCurrentSources':True},
        scope='UI inputs and tests changed; brand exported component bytes/geometry and API dependencies unchanged',
        manualGates=report['unverifiedGates']))
    # Exact batch delta from copied previous-candidate files, not the mixed Git diff.
    copy_root=PREVIOUS/'G2-01/run-g3a196yw/ui/web'
    patch=[]
    for name in changed:
        before=copy_root/Path(name).relative_to('ui/web')
        assert sha(before)==before_files[name]
        patch.extend(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(keepends=True),
            (ROOT/name).read_text(encoding='utf-8').splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name))
    patch.extend(difflib.unified_diff([], (ROOT/added[0]).read_text(encoding='utf-8').splitlines(keepends=True),
        fromfile='/dev/null',tofile='b/'+added[0]))
    (OUT/'implementation.patch').write_text(''.join(patch),encoding='utf-8')
    prior=read(PREVIOUS/'progress.json');index=copy.deepcopy(prior)
    for task in index['tasks']:
        task['evidencePaths']=[str(PREVIOUS.relative_to(ROOT)/p) for p in task['evidencePaths']]
        if task['taskId']=='G2-01':
            task.update(completedAt=readiness['finishedAt'],startedAt=readiness['startedAt'],
                evidencePaths=[readiness_path.relative_to(ROOT).as_posix()],
                note='Readiness rechecked on new UI bytes; 18 actual API/lifecycle checks passed.')
        if task['taskId'] in ('G2-02','G2-03','G4-01','G4-04'):
            task.update(status='running',completedAt=None,startedAt=None,startTimeRecorded=False,
                evidencePaths=[api_path.relative_to(ROOT).as_posix(),
                    (OUT/'client-replay.json').relative_to(ROOT).as_posix(),
                    (OUT/'chat-client.log').relative_to(ROOT).as_posix()],
                note='Automated portion completed; actual browser/focus/disconnect and other discovered defects remain. Not full task acceptance.')
    counts=dict(Counter(t['status'] for t in index['tasks']))
    assert counts=={'passed':5,'unverified':1,'running':4,'planned':20}
    index.update(candidateId=OUT.name,candidateSourceSetSha256=combined,generatedAt=now,
        previousWorkSheet=(PREVIOUS/'progress.json').relative_to(ROOT).as_posix(),
        statusCounts=counts,scope='New candidate status; prior evidence kept with its own candidate',
        overallStatus='local checks passed; G2-02/03 automated portion complete; browser and release unverified',
        nextTaskIds=['G2-04','G2-07'])
    write('progress.json',index)
    write('findings-update.json',dict(candidateId=OUT.name,findings=[dict(id='G0-F01',
        status='fixed-and-automatically-verified; browser acceptance pending',
        changedFiles=changed,testFile=added[0],
        reproduction='Previous submit cleared 4001-character draft and memory consent before API rejection; new regressions initially failed 4 of 5 cases.',
        behavior='4000 Unicode-code-point send limit; longer draft retained with bilingual feedback; direct retry cannot bypass limit.',
        remaining='Actual browser keyboard/input/focus and language behavior; other G0 findings unchanged')]))
    write('verification.json',dict(candidateId=OUT.name,finishedAt=now,sourceStable=True,previousSealedG0Unchanged=True,
        results={'javascript':480,'pythonUi':34,'isolatedBackend':43,'actualApiChecks':33,'requestCases':4,'clientReplays':4,'readinessChecks':18},
        limits=['controlled DOM/animator, no native paint or screen reader','no literal TCP stream disconnect this batch',
            'remote model and Cloudflare login untested','goal/ACT feature-on profiles not prepared'],
        releasePassed=False))
    assert all((ROOT/path).exists() for t in index['tasks'] for path in t['evidencePaths'])
    assert module.source_snapshot()==current
    print(json.dumps(dict(candidateId=OUT.name,statusCounts=counts,changed=changed,added=added,
                         sourceStable=True,previousEvidenceUnchanged=True),ensure_ascii=False))

if __name__=='__main__':main()
