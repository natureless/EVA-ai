"""python scripts/sync_obsidian.py --db data/eva.db --vault ."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connectors.obsidian import sync_vault
from memory.graph_projection import project_memory


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror a bounded EVA memory snapshot into an existing Obsidian vault")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=420, choices=range(1, 601), metavar="1..600")
    args = parser.parse_args()
    graph = project_memory(args.db, limit=args.limit, content_limit=100_000)
    result = sync_vault(graph, args.vault)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
