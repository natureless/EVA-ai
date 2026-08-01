"""Entry point for preview server — reads PORT from environment.

Usage:
    python app/run_preview.py

The PORT env var is set by the preview tool when autoPort is enabled.
Falls back to the configured port in settings.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import uvicorn

# Read PORT from environment (set by preview tool with autoPort)
port = int(os.environ.get("PORT", 0))
if not port:
    # Fall back to settings
    from app.config import settings
    port = settings.port

host = os.environ.get("HOST", "0.0.0.0")

if __name__ == "__main__":
    print(f"Starting EVA on {host}:{port}")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
    )
