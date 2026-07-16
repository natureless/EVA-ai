import json
import os
from pathlib import Path
from typing import Any


class SnapshotStore:
    def __init__(self, snapshot_dir: Path, latest_snapshot_path: Path) -> None:
        self.snapshot_dir = snapshot_dir
        self.latest_snapshot_path = latest_snapshot_path
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def load_latest(self) -> dict[str, Any] | None:
        if not self.latest_snapshot_path.exists():
            return None
        with self.latest_snapshot_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save_latest(self, payload: dict[str, Any]) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.latest_snapshot_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.latest_snapshot_path)
