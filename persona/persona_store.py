import json
import os
from pathlib import Path
from typing import Any


DEFAULT_PERSONA = {
    "name": "EVA",
    "tone": "precise",
    "initiative_level": "low",
    "safety_mode": "conservative",
}


class PersonaStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_or_init(self) -> dict[str, Any]:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        self.save(DEFAULT_PERSONA)
        return DEFAULT_PERSONA.copy()

    def save(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)
