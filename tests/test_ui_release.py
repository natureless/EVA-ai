"""Reject damaged and self-consistently altered delivery artifacts."""
import hashlib
import json
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

from scripts.check_ui_release import ROOT, STATIC, verify_archive


@pytest.fixture(scope="module")
def archive_data(tmp_path_factory):
    path = tmp_path_factory.mktemp("ui-release") / "brand.zip"
    subprocess.run(["node", "scripts/export_brand_kit.cjs", "--out", str(path)], cwd=ROOT, check=True, capture_output=True)
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    return entries, json.loads((STATIC / "brand-source.json").read_text(encoding="utf-8"))


def write_archive(path: Path, entries: dict):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo("fixture")
            info.filename = name  # Preserve the literal header name on Windows.
            archive.writestr(info, content)


def rewrite_manifest(entries, alter):
    updated = dict(entries)
    manifest = json.loads(updated["manifest.json"])
    alter(manifest, updated)
    updated["manifest.json"] = json.dumps(manifest).encode("utf-8")
    return updated


def update_file_record(manifest, name, payload):
    record = next(record for record in manifest["files"] if record["path"] == name)
    record.update(bytes=len(payload), crc32=f"{zlib.crc32(payload):08x}", sha256=hashlib.sha256(payload).hexdigest())


def test_canonical_source_package_passes_independent_reader(archive_data, tmp_path):
    entries, source = archive_data
    path = tmp_path / "valid.zip"
    write_archive(path, entries)
    report = verify_archive(path, source)
    assert report["files"] == 67
    assert report["pngDimensions"] == {}
    assert "not certified" in report["previewResources"]


@pytest.mark.parametrize("kind,reason", [("hash", "SHA-256 mismatch"), ("geometry", "Master geometry mismatch"),
                                         ("component", "Component source mismatch"), ("missing", "Incomplete brand catalog"),
                                         ("version", "Version mismatch")])
def test_corrupt_or_rehashed_artifacts_never_pass(archive_data, tmp_path, kind, reason):
    entries, source = archive_data

    def alter(manifest, content):
        if kind == "version":
            manifest["brandVersion"] = "999.0.0"
        elif kind == "missing":
            name = "components/cube-state.js"
            content.pop(name)
            manifest["files"] = [record for record in manifest["files"] if record["path"] != name]
        elif kind == "hash":
            manifest["files"][0]["sha256"] = "0" * 64
        else:
            name = "selected/eva-selected.svg" if kind == "geometry" else "components/cube-state.js"
            payload = content[name] + (b"\n<!-- altered -->" if kind == "geometry" else b"\n// altered")
            content[name] = payload
            update_file_record(manifest, name, payload)

    path = tmp_path / f"{kind}.zip"
    write_archive(path, rewrite_manifest(entries, alter))
    with pytest.raises(ValueError, match=reason):
        verify_archive(path, source)


@pytest.mark.parametrize("name", ["../outside", "icons//image", "icons/./image", "C:/image", "icons\\image"])
def test_ambiguous_archive_paths_are_rejected(archive_data, tmp_path, name):
    entries, source = archive_data
    invalid = dict(entries)
    invalid[name] = b"fixture"
    path = tmp_path / "invalid.zip"
    write_archive(path, invalid)
    with pytest.raises(ValueError, match="Unsafe archive path"):
        verify_archive(path, source)
