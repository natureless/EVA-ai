"""Local Markdown mirror. Obsidian edits never silently become EVA beliefs."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

INDEX = "EVA-Memory/Index.md"
MANIFEST = "EVA-Memory/.eva-manifest.json"


def open_uri(vault: Path, relative: str = INDEX) -> str:
    # Each device resolves its own copy of the identically named vault.
    return "obsidian://open?vault=" + quote(vault.resolve().name, safe="") + "&file=" + quote(relative.replace("\\", "/"), safe="")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _literal(value: str) -> str:
    # A code block keeps memory text (including HTML/wikilinks) inert in notes.
    fence = "`" * max(3, 1 + max((len(part) for part in re.findall(r"`+", value)), default=0))
    return fence + "text\n" + value + "\n" + fence + "\n"


def render_notes(graph: dict) -> dict[str, bytes]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    outgoing: dict[str, list] = {}
    for edge in graph["edges"]:
        if edge["source"] in nodes and edge["target"] in nodes:
            outgoing.setdefault(edge["source"], []).append(edge)
    files = {}
    for node in nodes.values():
        metadata = {
            "eva_id": node["id"], "eva_tier": node["tier"], "aliases": [node["label"]],
            "tags": ["eva-memory", "eva/" + node["tier"]], "source": node["source"],
            "epistemic_status": node["provenance"]["epistemic_status"],
            "updated_at": node["timestamp"], "content_truncated": node["content_truncated"],
        }
        frontmatter = "\n".join(key + ": " + json.dumps(value, ensure_ascii=False) for key, value in metadata.items())
        title = node["label"].replace("\n", " ").replace("[", "［").replace("]", "］")
        body = f"---\n{frontmatter}\n---\n\n# {title}\n\n" + _literal(node["content"])
        body += "\n## 来源记录\n\n" + _literal(json.dumps({"provenance": node["provenance"], "field_provenance": node["field_provenance"]}, ensure_ascii=False, indent=2))
        body += "\n## 已记录的出向关系\n\n"
        for edge in outgoing.get(node["id"], []):
            target = nodes[edge["target"]]
            alias = target["label"].translate(str.maketrans({"[": "［", "]": "］", "|": "｜", "#": "＃", "^": "＾"}))
            relation = str(edge["relation"]).replace("\n", " ").replace("[", "［").replace("]", "］")
            body += f"- {relation} → [[{target['note_path'][:-3]}|{alias}]] ({edge['kind']})\n"
            if edge.get("provenance"):
                body += "  - 关系来源状态：" + edge["provenance"]["epistemic_status"] + "\n"
        if not outgoing.get(node["id"]):
            body += "此展示范围内没有已记录的出向关系。\n"
        body += "\n> EVA 自动生成。手工修改会在下次同步时报告冲突并保留；不会回写 EVA 数据库。\n"
        files[node["note_path"]] = body.encode("utf-8")
    index = f"# EVA 记忆图谱\n\n生成时间：{graph['generated_at']}\n\n本次范围：{len(nodes)} 个节点，{len(graph['edges'])} 条连线。\n\n"
    index += "这是 EVA 数据的单向 Markdown 镜像。节点位置没有语义；连线来自已存储关系或明确的事件来源，不证明关系内容为真。\n\n"
    index += "在 Obsidian 关系图谱的搜索框输入 `path:EVA-Memory/notes`，即可只看 EVA 记忆。笔记别名保留可读名称。\n\n"
    index += "## 展示范围\n\n" + _literal(json.dumps({"counts": graph["counts"], "scope": graph["scope"]}, ensure_ascii=False, indent=2))
    index += "\n同步不删除旧笔记；上次导出后已退出当前范围的笔记可能仍在库中。以 `.eva-manifest.json` 的 current_files 为本次清单。\n"
    files[INDEX] = index.encode("utf-8")
    return files


def zip_notes(graph: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in render_notes(graph).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _contained(vault: Path, relative: str) -> Path:
    path = vault / relative
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError("mirror path escapes vault")
    return path


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".eva-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_vault(graph: dict, vault: Path) -> dict:
    """Only update previously generated, unmodified files; never delete notes."""
    vault = vault.resolve()
    if not (vault / ".obsidian").is_dir():
        raise ValueError("target must be an existing Obsidian vault (.obsidian directory)")
    manifest_path = _contained(vault, MANIFEST)
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    hashes = previous.get("hashes", {})
    if not isinstance(hashes, dict):
        raise ValueError("invalid mirror manifest")
    hashes = dict(hashes)
    generated = render_notes(graph)
    written, unchanged, conflicts = [], [], []
    for name, content in generated.items():
        target = _contained(vault, name)
        if target.exists():
            current = target.read_bytes()
            if current == content:
                unchanged.append(name)
                hashes[name] = _digest(content)
                continue
            if _digest(current) != hashes.get(name):
                conflicts.append(name)
                continue
        _atomic_write(target, content)
        hashes[name] = _digest(content)
        written.append(name)
    result = {"generated_at": graph["generated_at"], "written": len(written), "unchanged": len(unchanged),
              "conflicts": conflicts, "retained_outside_projection": len(set(hashes) - set(generated)),
              "nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "scope": graph["scope"]}
    _atomic_write(manifest_path, json.dumps({**result, "schema_version": 1, "hashes": hashes,
                  "current_files": list(generated)}, ensure_ascii=False, indent=2).encode("utf-8"))
    return {**result, "vault": str(vault), "open_uri": open_uri(vault)}
