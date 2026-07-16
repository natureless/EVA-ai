#!/usr/bin/env python
"""SQLite to PostgreSQL migration script.

Usage:
    python scripts/migrate_to_pg.py                    # dry run — print summary
    python scripts/migrate_to_pg.py --execute          # execute migration
    python scripts/migrate_to_pg.py --tables events,traces  # selective tables

Requires psycopg2-binary on the PG side. SQLite side uses built-in sqlite3.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args():
    p = argparse.ArgumentParser(description="SQLite → PostgreSQL migration")
    p.add_argument("--execute", action="store_true", help="actually run migration")
    p.add_argument("--tables", default="", help="comma-separated table names (default: all)")
    p.add_argument("--sqlite-path", default="data/eva.db", help="source SQLite path")
    p.add_argument("--batch-size", type=int, default=1000, help="rows per batch insert")
    return p.parse_args()


def get_pg_connection():
    """Connect to PostgreSQL using env vars."""
    import psycopg2
    host = os.environ.get("EVA_PG_HOST", "localhost")
    port = os.environ.get("EVA_PG_PORT", "5432")
    db = os.environ.get("EVA_PG_DATABASE", "eva")
    user = os.environ.get("EVA_PG_USER", "eva")
    pw = os.environ.get("EVA_PG_PASSWORD", "")
    return psycopg2.connect(host=host, port=port, dbname=db, user=user, password=pw)


def count_rows(conn, table):
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
    except Exception:
        return 0


def migrate_table(sqlite_conn, pg_conn, table, batch_size, dry_run):
    """Copy all rows from a SQLite table to its PG equivalent."""
    # get columns from SQLite
    cur = sqlite_conn.execute(f"SELECT * FROM {table} LIMIT 1")
    columns = [desc[0] for desc in cur.description]

    # count source rows
    total = count_rows(sqlite_conn, table)
    if total == 0:
        print(f"  {table}: empty, skipped")
        return 0

    # get all rows
    cur = sqlite_conn.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()

    if dry_run:
        print(f"  {table}: {total} rows (dry run — use --execute to migrate)")
        return total

    # batch insert into PG
    placeholders = ", ".join(["%s"] * len(columns))
    col_names = ", ".join(columns)
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    pg_cur = pg_conn.cursor()
    migrated = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        pg_cur.executemany(sql, batch)
        migrated += len(batch)
    pg_conn.commit()
    print(f"  {table}: {migrated}/{total} rows migrated")
    return migrated


TABLES = [
    "events",
    "episodic_memory",
    "semantic_memory",
    "traces",
    "persona_profiles",
    "memory_items",
    "memory_links",
    "working_memory",
    "long_term_memory",
    "world_entities",
    "world_edges",
    "executor_audit",
]


def main():
    args = parse_args()

    if args.tables:
        tables = [t.strip() for t in args.tables.split(",")]
    else:
        tables = TABLES

    if not os.path.exists(args.sqlite_path):
        print(f"Error: SQLite database not found at {args.sqlite_path}")
        sys.exit(1)

    import sqlite3
    sqlite_conn = sqlite3.connect(args.sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    print(f"Source: {args.sqlite_path}")
    print(f"Tables to migrate: {', '.join(tables)}")
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}")
    print()

    total = 0
    if args.execute:
        pg_conn = get_pg_connection()
        print(f"Target: PostgreSQL ({os.environ.get('EVA_PG_HOST', 'localhost')}:{os.environ.get('EVA_PG_PORT', '5432')}/{os.environ.get('EVA_PG_DATABASE', 'eva')})")
        print()

        for table in tables:
            total += migrate_table(sqlite_conn, pg_conn, table, args.batch_size, dry_run=False)
        pg_conn.close()
    else:
        for table in tables:
            total += migrate_table(sqlite_conn, None, table, args.batch_size, dry_run=True)

    sqlite_conn.close()
    print(f"\nTotal: {total} rows {'would be' if not args.execute else ''} migrated")


if __name__ == "__main__":
    main()
