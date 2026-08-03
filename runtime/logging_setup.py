import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def configure_logging(*, log_dir: Path, log_file: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_eva_configured", False):
        return

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_file if log_file.is_absolute() else log_dir / log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler._eva_handler = True  # type: ignore[attr-defined]

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._eva_handler = True  # type: ignore[attr-defined]

    root.setLevel(level.upper())
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel(level.upper())

    root._eva_configured = True  # type: ignore[attr-defined]


def shutdown_logging() -> None:
    """Close only handlers installed by EVA and allow clean reconfiguration."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if not getattr(handler, "_eva_handler", False):
            continue
        root.removeHandler(handler)
        handler.flush()
        handler.close()
    root._eva_configured = False  # type: ignore[attr-defined]
