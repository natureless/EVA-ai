"""Dependency rules that keep the stable EVA runtime understandable."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STABLE_PACKAGE_DIRS = (
    "app",
    "core",
    "event",
    "agents",
    "agent_os",
    "memory",
    "persona",
    "runtime",
    "world",
    "connectors",
)
DOMAIN_PACKAGE_DIRS = STABLE_PACKAGE_DIRS[1:]


def test_experimental_packages_have_one_stable_boundary() -> None:
    violations: list[str] = []
    allowed = PROJECT_ROOT / "app" / "experimental.py"
    for path in _python_files(STABLE_PACKAGE_DIRS):
        if path == allowed:
            continue
        for module in _imports(path):
            if module == "packages" or module.startswith("packages."):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} -> {module}")

    assert violations == [], (
        "Stable runtime code must access packages/* through app.experimental only:\n"
        + "\n".join(violations)
    )


def test_domain_packages_do_not_depend_on_application_layer() -> None:
    violations: list[str] = []
    for path in _python_files(DOMAIN_PACKAGE_DIRS):
        for module in _imports(path):
            if module == "app" or module.startswith("app."):
                violations.append(f"{path.relative_to(PROJECT_ROOT)} -> {module}")

    assert violations == [], (
        "Domain/runtime modules must receive configuration through injection:\n"
        + "\n".join(violations)
    )


def test_event_processing_is_independent_of_consumer_and_application() -> None:
    path = PROJECT_ROOT / "core" / "event_processor.py"
    forbidden = {"core.cognition_loop", "runtime.controller", "runtime.scheduler"}
    assert not forbidden.intersection(_imports(path))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # A processor receives one event; the selected scheduler owns queue ACKs.
    owned_queue_calls = [
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"consume", "task_done"}
    ]
    assert owned_queue_calls == []


def test_http_observation_does_not_inspect_worker_threads() -> None:
    violations = []
    for path in (PROJECT_ROOT / "app" / "api_routes").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"_threads", "_thread", "_future"}:
                violations.append(f"{path.name}:{node.lineno} -> {node.attr}")
    assert violations == [], "HTTP adapters must use public runtime observations"


def _python_files(package_dirs: tuple[str, ...]) -> list[Path]:
    return [
        path
        for directory in package_dirs
        for path in (PROJECT_ROOT / directory).rglob("*.py")
    ]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules
