#!/usr/bin/env python3
"""List opencode sessions for a given directory.

Usage:
  python3 opencode_list_sessions.py <directory>
  python3 opencode_list_sessions.py <directory> --json
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def find_db() -> Path:
    candidates = [
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        Path.home() / ".opencode" / "data" / "opencode.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return DB_PATH


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python3 opencode_list_sessions.py <directory> [--json]", file=sys.stderr)
        sys.exit(1)

    target = args[0]
    as_json = "--json" in args

    db = find_db()
    if not db.exists():
        print(f"Error: database not found at {db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT id, title, directory, time_created, time_updated, "
        "tokens_input, tokens_output, tokens_reasoning, agent "
        "FROM session "
        "WHERE directory LIKE ? "
        "ORDER BY time_created DESC",
        (f"%{target}%",),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No sessions found for directory matching '{target}'")
        return

    if as_json:
        result = []
        for r in rows:
            result.append({
                "id": r[0],
                "title": r[1],
                "directory": r[2],
                "created": datetime.fromtimestamp(r[3] / 1000).isoformat() if r[3] else None,
                "updated": datetime.fromtimestamp(r[4] / 1000).isoformat() if r[4] else None,
                "tokens_input": r[5],
                "tokens_output": r[6],
                "tokens_reasoning": r[7],
                "agent": r[8],
            })
        print(json.dumps(result, indent=2))
        return

    print(f"{'Session ID':<34} {'Title':<45} {'Agent':<10} {'Created':<22} {'Duration':<10} {'Tokens':>20}")
    print("-" * 141)
    for r in rows:
        sid, title, directory, created_ms, updated_ms, ti, to, tr, agent = r
        created = datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if created_ms else "N/A"
        duration = ""
        if created_ms and updated_ms:
            dur_s = (updated_ms - created_ms) / 1000
            if dur_s >= 3600:
                duration = f"{dur_s / 3600:.1f}h"
            elif dur_s >= 60:
                duration = f"{dur_s / 60:.1f}m"
            else:
                duration = f"{dur_s:.0f}s"
        tokens_str = f"{ti + to + tr:>10,}" if (ti or to or tr) else ""

        print(f"{sid:<34} {(title or '(untitled)'):<45} {(agent or '?'):<10} {created:<22} {duration:<10} {tokens_str:>20}")


if __name__ == "__main__":
    main()
