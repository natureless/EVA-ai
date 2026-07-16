import json
import os
from pathlib import Path
from typing import Any


DEFAULT_SELF_MODEL = {
    "identity": "EVA v0.1",
    "status": "active",
    "capabilities": [
        "chat",
        "docs_summary",
        "memory_write",
        "trace_record",
    ],
    "constraints": [
        "single_process",
        "single_round_single_agent",
    ],
}


class SelfModelStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_or_init(self) -> dict[str, Any]:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        self.save(DEFAULT_SELF_MODEL)
        return DEFAULT_SELF_MODEL.copy()

    def save(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)
