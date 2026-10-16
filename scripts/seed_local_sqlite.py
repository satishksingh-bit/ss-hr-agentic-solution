"""Seed the 6 isolated SQLite files emulating the 6 Firestore databases in Cloudtop mode (ADR-019, 03-deployment §2.1).

Uses strictly parameterized SQL queries (`?`).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hr_agent.models import FirestoreDatabaseId


def seed_sqlite_stores(state_dir: Path) -> list[str]:
    state_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    for db_enum in FirestoreDatabaseId:
        db_file = state_dir / f"{db_enum.value}.sqlite"
        conn = sqlite3.connect(str(db_file))
        try:
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS documents (collection_name TEXT NOT NULL, doc_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (collection_name, doc_id))"
            )
            if db_enum == FirestoreDatabaseId.HR_FLAGS:
                cur.execute(
                    "INSERT OR IGNORE INTO documents (collection_name, doc_id, payload_json) VALUES (?, ?, ?)",
                    (
                        "runtime_flags",
                        "global",
                        '{"writes_enabled": true, "assistant_enabled": true}',
                    ),
                )
            conn.commit()
            seeded.append(db_enum.value)
        finally:
            conn.close()
    return seeded


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed 6 isolated SQLite databases for local Cloudtop mode"
    )
    parser.add_argument("--state-dir", default=".local_state/local-staging/firestore")
    args = parser.parse_args()
    dbs = seed_sqlite_stores(Path(args.state_dir))
    print(f"Seeded {len(dbs)} isolated SQLite databases: {', '.join(dbs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
