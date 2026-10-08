"""Final source/evidence checks and immutable batch manifest."""
import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
baseline=read(OUT/'source-baseline.json')
for group in ('sourceHashes','apiAndDependencyHashes'):
    assert all(sha(ROOT/n)==h for n,h in baseline[group].items())
for script in OUT.glob('*.py'):ast.parse(script.read_text(encoding='utf-8'))
for doc in [OUT/'README.md',*[ROOT/'docs'/n for n in (
    'README.md','ui-logo-review-plan-20261008.md','ui-logo-task-breakdown-20261008.md',
    'ui-logo-development-plan.md','ui-logo-chat-acceptance.md')]]:
    for target in re.findall(r'\]\(([^)]+)\)',doc.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):continue
        assert (doc.parent/target.split('#')[0]).exists(),str((doc,target))
manifest_path=OUT/'artifact-manifest.json'
assert not manifest_path.exists(),'Do not overwrite sealed evidence'
evidence={p.relative_to(OUT).as_posix():sha(p) for p in OUT.iterdir() if p.is_file()}
for p in OUT.rglob('*.json'):
    if p.name in ('api-contracts.json','backend-tests.json','readiness.json','environment.json'):
        evidence[p.relative_to(OUT).as_posix()]=sha(p)
manifest=dict(candidateId=OUT.name,sealedAt=datetime.now(timezone.utc).isoformat(),
    artifactHashes=evidence,sourceSetSha256=baseline['sourceSetSha256'],
    scope='Batch evidence, including preparation failures; source/API fingerprints separately recorded. Not release approval.')
manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
assert all(sha(OUT/n)==h for n,h in evidence.items())
print(json.dumps(dict(status='passed',sealedArtifacts=len(evidence),sourcesStillMatch=True,markdownLinksExist=True),ensure_ascii=False))
