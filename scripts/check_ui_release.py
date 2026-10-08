"""Repeatable local UI/brand checks. This never certifies browser/manual gates."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ui/web/static"
VERSIONS = ("formatVersion", "brandVersion", "componentVersion", "tokenSchemaVersion")


def source_snapshot() -> dict:
    files = [path for folder in (STATIC, ROOT / "ui/web/templates", ROOT / "tests/js")
             for path in folder.iterdir() if path.is_file()]
    files += [ROOT / name for name in ("scripts/check_ui_release.py", "scripts/build_brand_assets.cjs",
              "scripts/export_brand_kit.cjs", "scripts/cube_ui_probe.js", "tests/test_ui_release.py",
              "tests/test_memory_graph.py", "app/memory_preview.py")]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(files))}


class LocalReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "img", "link"}:
            for key in ("src", "href"):
                if key in values:
                    self.references.append(values[key])


def verify_archive(path: Path, source: dict, expected_selection: dict | None = None) -> dict:
    """Independently read a generated ZIP; compare every byte to its manifest."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate archive paths")
        for info in archive.infolist():
            name = info.orig_filename
            parts = PurePosixPath(name)
            if not name or parts.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")) or "\\" in name or ":" in name or any(ord(char)<32 for char in name):
                raise ValueError("Unsafe archive path")
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Corrupt ZIP entry: {bad}")
        manifest = json.loads(archive.read("manifest.json"))
        for key in VERSIONS:
            if manifest.get(key) != source[key]:
                raise ValueError(f"Version mismatch: {key}")
        if manifest.get("provenance") != source["provenance"]:
            raise ValueError("Brand source mismatch")
        if manifest.get("assetState") != "solved" or manifest.get("clearSpaceRatio") != .25:
            raise ValueError("Invalid standard-state declaration")
        if expected_selection is not None and manifest.get("selected") != expected_selection:
            raise ValueError("Export selection mismatch")
        selected = manifest.get("selected", {})
        if (set(selected) != {"order", "variant", "view", "size"} or selected["order"] not in (2, 3)
                or selected["variant"] not in ("primary", "monochrome", "dark", "light", "favicon")
                or selected["view"] not in ("front", "side", "top", "iso", "oblique", "bottom")
                or selected["size"] not in (256, 512, 1024, 2048)
                or (selected["variant"] == "favicon" and (selected["order"] != 2 or selected["view"] != "iso"))):
            raise ValueError("Invalid brand selection")
        records = manifest["files"]
        if len({record["path"] for record in records}) != len(records):
            raise ValueError("Duplicate manifest paths")
        if set(names) != {record["path"] for record in records} | {"manifest.json"}:
            raise ValueError("Manifest coverage mismatch")
        for record in records:
            payload = archive.read(record["path"])
            if len(payload) != record["bytes"] or f"{zlib.crc32(payload):08x}" != record["crc32"]:
                raise ValueError(f"Byte/CRC mismatch: {record['path']}")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise ValueError(f"SHA-256 mismatch: {record['path']}")
        required = {f"svg/{order}x{order}/{variant}/{view}.svg"
                    for order in (2, 3) for variant in ("primary", "monochrome", "dark", "light")
                    for view in ("front", "side", "top", "iso", "oblique", "bottom")}
        required |= {f"icons/favicon-{size}.svg" for size in (16, 32, 48)}
        required |= {"selected/eva-selected.svg", "tokens.json", "README.md", "CHANGELOG.md", "preview.html"}
        required |= {"components/" + name for name in source["provenance"]["sources"]}
        if not required <= set(names):
            raise ValueError("Incomplete brand catalog")
        for name, digest in source["provenance"]["sources"].items():
            if hashlib.sha256(archive.read("components/" + name)).hexdigest() != digest:
                raise ValueError(f"Component source mismatch: {name}")
        # A manifest can be edited along with an asset. Bind the actual marks to
        # the canonical geometry instead of accepting echoed provenance claims.
        renderer = """const fs=require('node:fs'), kit=require('./ui/web/static/brand-package.js'),
assets=require('./ui/web/static/brand-assets.js'), brand=require('./ui/web/static/logo-tokens.js');
const selected=JSON.parse(fs.readFileSync(0,'utf8'));
const marks=Object.fromEntries(kit.catalog().map(entry=>[entry.name,assets.svg(entry.options)]));
marks['selected/eva-selected.svg']=assets.svg(selected);
process.stdout.write(JSON.stringify({marks,tokens:JSON.stringify(brand.tokens,null,2)+'\\n'}));"""
        generated = subprocess.run(["node", "-e", renderer], cwd=ROOT, input=json.dumps(selected),
                                   capture_output=True, text=True, encoding="utf-8", check=True)
        canonical = json.loads(generated.stdout)
        for name, markup in canonical["marks"].items():
            if archive.read(name) != markup.encode("utf-8"):
                raise ValueError(f"Master geometry mismatch: {name}")
        if archive.read("tokens.json") != canonical["tokens"].encode("utf-8"):
            raise ValueError("Canonical Token mismatch")
        for name in names:
            if name.endswith(".svg"):
                svg = ElementTree.fromstring(archive.read(name))
                if svg.tag != "{http://www.w3.org/2000/svg}svg" or not svg.get("viewBox"):
                    raise ValueError(f"Invalid SVG: {name}")
        html = LocalReferences()
        html.feed(archive.read("preview.html").decode("utf-8"))
        for reference in html.references:
            if re.match(r"(?:[a-z]+:|//|/)", reference, re.I) or reference not in names:
                raise ValueError(f"Nonlocal/missing preview resource: {reference}")
        png_names = {name for name in names if name.endswith(".png")}
        if bool(png_names) != manifest.get("pngIncluded"):
            raise ValueError("PNG declaration mismatch")
        png_dimensions = {}
        if png_names:
            from PIL import Image

            expected_png = {"selected/eva-selected.png": manifest["selected"]["size"]}
            expected_png.update({f"icons/favicon-{size}.png": size for size in (16, 32, 48)})
            if png_names != set(expected_png):
                raise ValueError("Incomplete PNG catalog")
            for name, size in expected_png.items():
                with Image.open(io.BytesIO(archive.read(name))) as image:
                    image.load()
                    if image.format != "PNG" or image.size != (size, size) or "A" not in image.getbands():
                        raise ValueError(f"Invalid transparent PNG: {name}")
                    alpha = image.getchannel("A")
                    if alpha.getextrema() != (0, 255) or any(alpha.getpixel(point) != 0 for point in ((0, 0), (size-1, 0), (0, size-1), (size-1, size-1))):
                        raise ValueError(f"Missing transparent clear space/content: {name}")
                    png_dimensions[name] = list(image.size)
        return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "files": len(names), "versions": {key: manifest[key] for key in VERSIONS},
                "selected": manifest["selected"], "pngDimensions": png_dimensions,
                "previewResources": "local references verified; browser execution not certified"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--browser-zip", type=Path, action="append", default=[])
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.out_dir or ROOT / "artifacts/ui-release" / stamp).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"date": datetime.now(timezone.utc).isoformat(), "status": "checking", "checks": [], "archives": [],
              "unverifiedGates": ["Native Chrome/Edge save and direct offline execution", "Reliable foreground ten-minute 2x2/3x3 performance",
                                  "Native 200% zoom", "Real screen-reader acceptance"],
              "scope": "Local automated checks only; not approval to publish or full release certification"}
    before = source_snapshot()

    def command(label: str, argv: list[str]):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (output / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        report["checks"].append({"name": label, "exitCode": result.returncode, "command": argv})
        if result.returncode:
            raise RuntimeError(f"{label} failed; see {output / (label+'.log')}")

    try:
        command("javascript", ["node", "--test", *[str(path) for path in sorted((ROOT / "tests/js").glob("*.cjs"))]])
        command("generated-assets", ["node", "scripts/build_brand_assets.cjs", "--check"])
        command("python-ui", [sys.executable, "-m", "pytest", "tests/test_ui_release.py", "tests/test_memory_graph.py", "-q"])
        source = json.loads((STATIC / "brand-source.json").read_text(encoding="utf-8"))
        for order, variant in ((2, "primary"), (3, "dark")):
            path = output / f"eva-{order}x{order}-source.zip"
            command(f"export-{order}", ["node", "scripts/export_brand_kit.cjs", "--out", str(path), "--order", str(order), "--variant", variant, "--view", "iso", "--size", "2048"])
            report["archives"].append(verify_archive(path, source, {"order": order, "variant": variant, "view": "iso", "size": 2048}))
        for path in args.browser_zip:
            report["archives"].append(verify_archive(path, source))
        if source_snapshot() != before:
            raise RuntimeError("Checked sources changed during validation; rerun against a stable tree")
        report["status"] = "local-checks-passed; manual-gates-unverified"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        report["sourceHashes"] = before
        report["sourcesStable"] = source_snapshot() == before
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {output / 'report.json'}")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
