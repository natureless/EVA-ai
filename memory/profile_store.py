import json
from pathlib import Path
from typing import Any

from runtime.file_utils import atomic_json_save


DEFAULT_PROFILE = {
    "user_name": None,
    "preferences": {},
    "projects": [],
}


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_or_init(self) -> dict[str, Any]:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        self.save(DEFAULT_PROFILE)
        return DEFAULT_PROFILE.copy()

    def save(self, payload: dict[str, Any]) -> None:
        atomic_json_save(self.path, payload)
