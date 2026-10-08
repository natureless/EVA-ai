"""Create and verify recovery copies; never overwrite a configured live store.

Run from the project root: python -m scripts.world_recovery --help
"""

import argparse
import json
from pathlib import Path
import sqlite3

from world.recovery_tools import prepare_recovery, restore_recovery, verify_recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="exact backups, conflict report and candidate copy")
    prepare.add_argument("--snapshot", required=True, type=Path)
    prepare.add_argument("--db", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path, help="new directory; existing paths are refused")
    verify = commands.add_parser("verify", help="verify hashes, integrity and reconstructed state")
    verify.add_argument("--bundle", required=True, type=Path)
    restore = commands.add_parser("restore", help="restore to a new isolated directory")
    restore.add_argument("--bundle", required=True, type=Path)
    restore.add_argument("--output", required=True, type=Path)
    restore.add_argument("--variant", choices=("original", "candidate"), default="original")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare_recovery(args.snapshot, args.db, args.output)
        elif args.command == "verify":
            result = verify_recovery(args.bundle)
        else:
            result = restore_recovery(args.bundle, args.output, variant=args.variant)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Recovery failed: {exc}\n")
    # Counters only; detailed conflict samples remain in the private bundle.
    summary = {key: {k: v for k, v in value.items() if k != "conflict_samples"} if key in {"entities", "edges"} else value for key, value in result.items()}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
