"""Validate saved G0 evidence and seal its artifact hashes; no service/browser calls."""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]

def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    manifest_path = OUT/'artifact-manifest.json'
    if manifest_path.exists():
        raise ValueError('Evidence already sealed; verify recorded hashes without rewriting the manifest')
    baseline = read('source-baseline.json')
    index = read('acceptance-index.json')
    controls = read('control-inventory.json')
    surfaces = read('surfaces.json')['surfaces']
    apis = read('api-map.json')['apis']
    findings = read('findings.json')['findings']
    validation = read('g0-validation.json')
    assert baseline['candidateId'] == index['candidateId'] == OUT.name
    assert baseline['sourceSetSha256'] == index['candidateSourceSetSha256']
    assert baseline['referenceReport']['comparison']['exactMatch']
    for group in ('sourceHashes', 'apiAndDependencyHashes'):
        for path, expected in baseline[group].items():
            assert sha(ROOT/path) == expected, f'Source changed: {path}'
    assert sha(OUT/'capture_g0.py') == baseline['captureScriptSha256']
    report = baseline['referenceReport']
    assert sha(ROOT/report['path']) == report['sha256']
    for package in baseline['packages']:
        assert sha(ROOT/package['path']) == package['sha256']
    assert [p['validation']['files'] for p in baseline['packages']] == [67, 67, 71]
    tasks = index['tasks']
    task_ids = {t['taskId'] for t in tasks}
    surface_ids = {s['surfaceId'] for s in surfaces}
    assert len(tasks) == len(task_ids) == 30
    assert len(surface_ids) == len(surfaces) == 35
    assert Counter(t['status'] for t in tasks) == {'passed': 4, 'planned': 26}
    for task in tasks:
        assert set(task['dependsOnTasks']) <= task_ids
        for path in task['evidencePaths']:
            assert (OUT/path).is_file(), path
        if task['status'] == 'passed':
            assert task['taskId'].startswith('G0-')
            assert task['completedAt'] == validation['finishedAt']
        else:
            assert task['completedAt'] is None
    assert len(controls['sourceControls']) == 203
    assert len(controls['dynamicControlFamilies']) == 8
    assert all(c['surfaceId'] in surface_ids for c in controls['sourceControls'])
    assert not any(c['classification'] == 'page-default' for c in controls['sourceControls'])
    for surface in surfaces:
        assert set(surface['tasks']) <= task_ids
    assert len(apis) == len({a['apiId'] for a in apis}) == 32
    for api in apis:
        assert set(api['surfaceIds']) <= surface_ids
        assert (ROOT/api['clientRef']['path']).is_file()
        assert (ROOT/api['serverRef']['source']).is_file()
        assert all((ROOT/p).is_file() for p in api['testFiles'])
    assert len(findings) == 6
    for finding in findings:
        assert set(finding['tasks']) <= task_ids
        assert all((ROOT/r['path']).is_file() for r in finding['refs'])
    docs = [ROOT/'docs'/name for name in (
        'README.md', 'ui-logo-review-plan-20261008.md',
        'ui-logo-task-breakdown-20261008.md', 'ui-logo-development-plan.md')]
    for path in list(OUT.glob('*.md')) + docs:
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            if path.parent == OUT and target == 'artifact-manifest.json':
                continue  # This file is generated only after all other checks pass.
            assert (path.parent/target.split('#')[0]).exists(), f'Broken link: {path.name} {target}'
    result = dict(candidateId=OUT.name, verifiedAt=datetime.now(timezone.utc).isoformat(),
        status='passed', sourceHashesStillMatch=True, artifactReferencesExist=True,
        taskAndSurfaceMappingValid=True, allMarkdownFileLinksExist=True,
        scope='G0 evidence only; no new product regression, browser or API execution')
    (OUT/'final-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    artifacts = {p.name: sha(p) for p in sorted(OUT.iterdir()) if p.is_file() and p != manifest_path}
    manifest = dict(schemaVersion=1, candidateId=OUT.name, sealedAt=result['verifiedAt'],
        artifactHashes=artifacts, supportingDocumentHashes={p.relative_to(ROOT).as_posix():sha(p) for p in docs},
        note='Initial G0 evidence bytes; later task updates require explicit history, not recapture over this candidate.')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assert manifest_path.is_file()
    assert all(sha(OUT/name) == expected for name, expected in artifacts.items())
    print(json.dumps(dict(result, sealedArtifacts=len(artifacts)), ensure_ascii=False))

if __name__ == '__main__':
    main()
