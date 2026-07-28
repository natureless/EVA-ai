import json
from pathlib import Path
from typing import Any

from runtime.file_utils import atomic_json_save


class SnapshotStore:
    def __init__(self, snapshot_dir: Path, latest_snapshot_path: Path) -> None:
        self.snapshot_dir = snapshot_dir
        self.latest_snapshot_path = latest_snapshot_path
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def load_latest(self) -> dict[str, Any] | None:
        if not self.latest_snapshot_path.exists():
            return None
        with self.latest_snapshot_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)  # type: ignore[no-any-return]

    def save_latest(self, payload: dict[str, Any]) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        atomic_json_save(self.latest_snapshot_path, payload)
