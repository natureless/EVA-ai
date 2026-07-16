"""Shared file-system utilities."""

import json
import os
from pathlib import Path
from typing import Any


def atomic_json_save(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    **kwargs: Any,
) -> None:
    """Atomically write JSON data to a file via temp + rename.

    Writes to path.with_suffix('.json.tmp'), then os.replace() to
    the target path. This avoids partial writes on crash and keeps
    readers from seeing half-written files.
    """
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, **kwargs)
    os.replace(tmp, path)
