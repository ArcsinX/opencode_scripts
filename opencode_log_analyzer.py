#!/usr/bin/env python3
"""Analyze an opencode session export JSON and print timing/token statistics.

Recursively includes tool usage from child sessions (subagents).

Usage:
  opencode export <sessionID> | python3 opencode_log_analyzer.py
  python3 opencode_log_analyzer.py < export.json
  python3 opencode_log_analyzer.py <sessionID>
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
OPENCODE_BIN = Path.home() / ".opencode" / "bin" / "opencode"


def find_db() -> Path:
    """Find the opencode session database."""
    candidates = [
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        Path.home() / ".opencode" / "data" / "opencode.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return DB_PATH


def get_child_ids(session_id: str) -> list[str]:
    """Query the session DB for direct child sessions."""
    db = find_db()
    if not db.exists():
        return []
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT id FROM session WHERE parent_id = ?", (session_id,)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_session_info(session_id: str) -> dict:
    """Get token counts from DB for a session."""
    db = find_db()
    if not db.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT tokens_input, tokens_output, tokens_reasoning, "
            "tokens_cache_read, tokens_cache_write, "
            "time_created, time_updated "
            "FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            return {
                "tokens": {
                    "input": row[0] or 0,
                    "output": row[1] or 0,
                    "reasoning": row[2] or 0,
                    "cache": {
                        "read": row[3] or 0,
                        "write": row[4] or 0,
                    },
                },
                "time": {
                    "created": row[5] or 0,
                    "updated": row[6] or 0,
                },
            }
    except Exception:
        pass
    return {}


def export_session(session_id: str) -> dict:
    """Export a session via the opencode CLI into a temp file (avoids pipe buffer limits)."""
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp = f.name
        subprocess.run(
            [str(OPENCODE_BIN), "export", session_id, "--sanitize"],
            stdout=open(tmp, "w"), stderr=subprocess.DEVNULL, timeout=60,
        )
        with open(tmp) as f:
            data = json.load(f)
        return data
    except Exception:
        return {}
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def parse_session(data: dict) -> dict:
    """Extract tool times, counts, thinking time, and token info from session data."""
    info = data.get("info", {})
    session_tokens = info.get("tokens", {})

    tool_times: dict[str, float] = {}
    tool_counts: dict[str, int] = {}
    thinking_time = 0.0
    first_time = None
    last_time = None

    for msg in data.get("messages", []):
        msg_info = msg.get("info", {})
        msg_time = msg_info.get("time", {})
        mc = msg_time.get("created")
        if mc and (first_time is None or mc < first_time):
            first_time = mc
        mcomp = msg_time.get("completed")
        if mcomp and (last_time is None or mcomp > last_time):
            last_time = mcomp

        for part in msg.get("parts", []):
            ptype = part.get("type")
            if ptype == "tool":
                tool_name = part.get("tool", "unknown")
                state = part.get("state", {})
                t = state.get("time", {})
                start = t.get("start")
                end = t.get("end")
                if start and end:
                    dur = end - start
                    tool_times[tool_name] = tool_times.get(tool_name, 0) + dur
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif ptype == "reasoning":
                t = part.get("time", {})
                start = t.get("start")
                end = t.get("end")
                if start and end:
                    thinking_time += end - start

    return {
        "tool_times": tool_times,
        "tool_counts": tool_counts,
        "thinking_time": thinking_time,
        "session_tokens": session_tokens,
        "first_time": first_time,
        "last_time": last_time,
        "info_time": {
            "created": info.get("time", {}).get("created"),
            "updated": info.get("time", {}).get("updated"),
        },
    }


def merge_results(results: list[dict]) -> dict:
    """Merge multiple parse results together."""
    merged = {
        "tool_times": {},
        "tool_counts": {},
        "thinking_time": 0.0,
        "session_tokens": {
            "input": 0, "output": 0, "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
        "first_time": None,
        "last_time": None,
        "info_time": {"created": None, "updated": None},
    }

    for r in results:
        for tool, t in r["tool_times"].items():
            merged["tool_times"][tool] = merged["tool_times"].get(tool, 0) + t
        for tool, c in r["tool_counts"].items():
            merged["tool_counts"][tool] = merged["tool_counts"].get(tool, 0) + c

        merged["thinking_time"] += r["thinking_time"]

        st = r.get("session_tokens", {})
        if st:
            merged["session_tokens"]["input"] += st.get("input", 0)
            merged["session_tokens"]["output"] += st.get("output", 0)
            merged["session_tokens"]["reasoning"] += st.get("reasoning", 0)
            cache = st.get("cache", {})
            if cache:
                merged["session_tokens"]["cache"]["read"] += cache.get("read", 0)
                merged["session_tokens"]["cache"]["write"] += cache.get("write", 0)

        ft = r.get("first_time")
        if ft and (merged["first_time"] is None or ft < merged["first_time"]):
            merged["first_time"] = ft
        lt = r.get("last_time")
        if lt and (merged["last_time"] is None or lt > merged["last_time"]):
            merged["last_time"] = lt

        it = r.get("info_time", {})
        ic = it.get("created")
        if ic and (merged["info_time"]["created"] is None or ic < merged["info_time"]["created"]):
            merged["info_time"]["created"] = ic
        iu = it.get("updated")
        if iu and (merged["info_time"]["updated"] is None or iu > merged["info_time"]["updated"]):
            merged["info_time"]["updated"] = iu

    return merged


def print_results(merged: dict, label: str = ""):
    """Print formatted results."""
    tool_times = merged["tool_times"]
    tool_counts = merged["tool_counts"]
    thinking_time = merged["thinking_time"]
    session_tokens = merged["session_tokens"]
    first_time = merged["first_time"]
    last_time = merged["last_time"]
    info_time = merged["info_time"]

    all_tools = sum(tool_times.values())

    if label:
        print(f"=== {label} ===")
        print()

    total_time_ms = 0
    created = info_time.get("created")
    updated = info_time.get("updated")
    if created and updated:
        total_time_ms = updated - created

    print(f"{'Metric':<30} {'Value':>15}")
    print("-" * 47)

    if total_time_ms > 0:
        print(f"{'Total session time':<30} {total_time_ms / 1000:>15.3f}s")
    elif first_time and last_time:
        print(f"{'Total session time':<30} {(last_time - first_time) / 1000:>15.3f}s")
    else:
        print(f"{'Total session time':<30} {'N/A':>15}")

    print(f"{'Grep tool total':<30} {tool_times.get('grep', 0) / 1000:>15.3f}s")
    print(f"{'LSP tool total':<30} {tool_times.get('lsp', 0) / 1000:>15.3f}s")
    print(f"{'All tools total':<30} {all_tools / 1000:>15.3f}s")
    print(f"{'Thinking total':<30} {thinking_time / 1000:>15.3f}s")

    if session_tokens:
        inp = session_tokens.get("input", 0)
        out = session_tokens.get("output", 0)
        reas = session_tokens.get("reasoning", 0)
        cache_read = session_tokens.get("cache", {}).get("read", 0)
        cache_write = session_tokens.get("cache", {}).get("write", 0)
        total_tok = inp + out + reas
        print()
        print(f"{'Token':<30} {'Count':>15}")
        print("-" * 47)
        print(f"{'Input':<30} {inp:>15,}")
        print(f"{'Output':<30} {out:>15,}")
        print(f"{'Reasoning':<30} {reas:>15,}")
        print(f"{'Total (input+output+reasoning)':<30} {total_tok:>15,}")
        if cache_read:
            print(f"{'Cache read':<30} {cache_read:>15,}")
        if cache_write:
            print(f"{'Cache write':<30} {cache_write:>15,}")

    print()
    print(f"{'Tool':<20} {'Calls':>7} {'Total Time':>12}")
    print("-" * 40)
    for tool_name in sorted(tool_times, key=lambda t: tool_times[t], reverse=True):
        print(f"{tool_name:<20} {tool_counts.get(tool_name, 0):>7} {tool_times[tool_name] / 1000:>12.3f}s")


def process_session(session_id: str, depth: int = 0) -> list[dict]:
    """Recursively process a session and all its children."""
    data = export_session(session_id)
    if not data:
        return []

    result = parse_session(data)

    child_ids = get_child_ids(session_id)
    all_results = [result]

    for cid in child_ids:
        child_results = process_session(cid, depth + 1)
        all_results.extend(child_results)

    return all_results


def main():
    args = sys.argv[1:]

    # Pipe mode: opencode export <id> | python3 script.py
    if not sys.stdin.isatty() and not args:
        tmp = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="wb") as f:
                tmp = f.name
                f.write(sys.stdin.buffer.read())
            with open(tmp) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            if tmp:
                sys.stderr.write(
                    f"Error: JSON parse failed. Piped data might be truncated "
                    f"(pipe buffer limit on macOS is 128KB). "
                    f"Raw data saved to {tmp} for inspection.\n"
                    f"Try: python3 script.py <sessionID> instead.\n"
                )
            sys.exit(1)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
        result = parse_session(data)
        print_results(result)
        return

    # Session ID mode: python3 script.py <sessionID>
    if args:
        session_id = args[0]
        all_results = process_session(session_id)
        if not all_results:
            print(f"Error: could not export session '{session_id}'", file=sys.stderr)
            sys.exit(1)
        nchild = len(all_results) - 1
        label = f"Session {session_id}"
        if nchild > 0:
            label += f" (including {nchild} child session{'s' if nchild > 1 else ''})"
        merged = merge_results(all_results)
        print_results(merged, label=label)
        return

    # No input
    print(__doc__, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
