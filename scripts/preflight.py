#!/usr/bin/env python3
"""EVA-MVSC 启动前校验脚本。

在部署前运行，检查:
1. Python 版本
2. 依赖完整性
3. 配置文件存在性
4. 数据目录权限
5. constitution.yaml 有效性
6. 端口可用性
7. 数据库完整性
8. MVSC 组件导入

Usage:
    python scripts/preflight.py
    python scripts/preflight.py --mvsc  # also check MVSC components
"""

import argparse
import os
import socket
import sys
from pathlib import Path


def check(msg: str, ok: bool, detail: str = "") -> bool:
    status = "✅" if ok else "❌"
    print(f"  {status} {msg}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def main():
    parser = argparse.ArgumentParser(description="EVA-MVSC preflight check")
    parser.add_argument("--mvsc", action="store_true", help="Check MVSC components")
    args = parser.parse_args()

    # Ensure project root is on path for MVSC imports
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    print("EVA-MVSC Preflight Check")
    print("=" * 50)
    all_ok = True

    # 1. Python version
    py_ver = sys.version_info
    all_ok &= check(
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        py_ver >= (3, 11),
        "Need Python 3.11+",
    )

    # 2. Key dependencies
    deps = ["fastapi", "pydantic", "uvicorn", "yaml", "httpx"]
    for dep in deps:
        try:
            __import__(dep)
            all_ok &= check(f"Import {dep}", True)
        except ImportError:
            all_ok &= check(f"Import {dep}", False, "Not installed")

    # 3. Config files
    config_files = [
        "constitution.yaml",
        "config/policy.yaml",
        "config/executors.yaml",
        "config/system_prompt.yaml",
    ]
    for f in config_files:
        exists = Path(f).exists()
        all_ok &= check(f"Config: {f}", exists, "File missing")

    # 4. Data directory
    data_dir = Path("data")
    if data_dir.exists():
        writable = os.access(data_dir, os.W_OK)
        all_ok &= check("data/ writable", writable, "Permission denied")
    else:
        try:
            data_dir.mkdir(parents=True)
            all_ok &= check("data/ created", True)
        except OSError:
            all_ok &= check("data/ creatable", False, "Cannot create")

    # 5. Constitution validity
    const_path = Path("constitution.yaml")
    if const_path.exists():
        import yaml
        try:
            with const_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            has_version = "version" in (data or {})
            all_ok &= check("constitution.yaml valid", has_version, "Missing version field")
        except Exception as e:
            all_ok &= check("constitution.yaml parse", False, str(e))

    # 6. Port availability
    port = int(os.environ.get("EVA_PORT", 8000))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        all_ok &= check(f"Port {port} available", True)
    except OSError:
        all_ok &= check(f"Port {port} available", False, "Port in use")

    # 7. Database integrity (if exists)
    db_path = Path(os.environ.get("EVA_DB_PATH", "data/eva.db"))
    if db_path.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA integrity_check")
            conn.close()
            all_ok &= check("Database integrity", True)
        except Exception as e:
            all_ok &= check("Database integrity", False, str(e))

    # 8. MVSC components
    if args.mvsc:
        mvsc_modules = [
            "packages.contracts.events",
            "packages.contracts.state",
            "packages.kernel.event_store",
            "packages.kernel.event_bus_adapter",
            "packages.kernel.state_bridge",
            "packages.cognition.loop",
            "packages.cognition.adapted_loop",
        ]
        for mod in mvsc_modules:
            try:
                __import__(mod)
                all_ok &= check(f"MVSC: {mod.split('.')[-1]}", True)
            except ImportError as e:
                all_ok &= check(f"MVSC: {mod.split('.')[-1]}", False, str(e))

    print("=" * 50)
    if all_ok:
        print("✅ All checks passed — system ready for deployment")
    else:
        print("❌ Some checks failed — fix issues before deployment")
        sys.exit(1)


if __name__ == "__main__":
    main()
