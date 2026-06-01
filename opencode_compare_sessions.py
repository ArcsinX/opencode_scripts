#!/usr/bin/env python3
"""Compare two sets of opencode sessions side by side.

Each set is a comma-separated list of session IDs. Children are included recursively.

Usage:
  python3 opencode_compare_sessions.py <sessionID1>,<sessionID2> <sessionID3>
  python3 opencode_compare_sessions.py ses1,ses2,ses3 ses4,ses5,ses6
"""

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
OPENCODE_BIN = Path.home() / ".opencode" / "bin" / "opencode"


def find_db() -> Path:
    candidates = [
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        Path.home() / ".opencode" / "data" / "opencode.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return DB_PATH


def get_child_ids(session_id: str) -> list[str]:
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


def get_session_title(session_id: str) -> str:
    try:
        db = find_db()
        if db.exists():
            conn = sqlite3.connect(str(db))
            row = conn.execute(
                "SELECT title FROM session WHERE id = ?", (session_id,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return session_id


def export_session(session_id: str) -> dict:
    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp = f.name
        subprocess.run(
            [str(OPENCODE_BIN), "export", session_id, "--sanitize"],
            stdout=open(tmp, "w"), stderr=subprocess.DEVNULL, timeout=60,
        )
        with open(tmp) as f:
            return __import__("json").load(f)
    except Exception:
        return {}
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def parse_session(data: dict) -> dict:
    info = data.get("info", {})
    session_tokens = info.get("tokens", {})

    tool_times: dict[str, float] = {}
    tool_counts: dict[str, int] = {}
    thinking_time = 0.0
    first_time = info.get("time", {}).get("created")
    last_time = info.get("time", {}).get("updated")

    for msg in data.get("messages", []):
        for part in msg.get("parts", []):
            ptype = part.get("type")
            if ptype == "tool":
                tool_name = part.get("tool", "unknown")
                t = part.get("state", {}).get("time", {})
                start = t.get("start")
                end = t.get("end")
                if start and end:
                    tool_times[tool_name] = tool_times.get(tool_name, 0) + (end - start)
                    tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif ptype == "reasoning":
                t = part.get("time", {})
                start = t.get("start")
                end = t.get("end")
                if start and end:
                    thinking_time += end - start

    dur = (last_time - first_time) if first_time and last_time else 0
    return {
        "tool_times": tool_times,
        "tool_counts": tool_counts,
        "thinking_time": thinking_time,
        "session_tokens": session_tokens,
        "first_time": first_time,
        "last_time": last_time,
        "duration": dur,
    }


def merge_results(results: list[dict]) -> dict:
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
        "total_duration": 0.0,
        "session_count": len(results),
        "titles": [],
    }
    for r in results:
        for tool, t in r["tool_times"].items():
            merged["tool_times"][tool] = merged["tool_times"].get(tool, 0) + t
        for tool, c in r["tool_counts"].items():
            merged["tool_counts"][tool] = merged["tool_counts"].get(tool, 0) + c
        merged["thinking_time"] += r["thinking_time"]
        merged["total_duration"] += r.get("total_duration") or r.get("duration", 0)
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
    return merged


def process_session(session_id: str) -> dict:
    data = export_session(session_id)
    if not data:
        return {}
    result = parse_session(data)
    child_ids = get_child_ids(session_id)
    all_results = [result]
    for cid in child_ids:
        cdata = export_session(cid)
        if cdata:
            all_results.append(parse_session(cdata))
    merged = merge_results(all_results)
    merged["titles"] = [get_session_title(session_id)]
    merged["session_count"] = 1 + len(child_ids)
    return merged


def process_set(session_ids: list[str]) -> dict:
    all_merged = []
    titles = []
    for sid in session_ids:
        r = process_session(sid)
        if r:
            all_merged.append(r)
            titles.extend(r.get("titles", [get_session_title(sid)]))
    merged = merge_results(all_merged)
    merged["titles"] = titles
    merged["session_count"] = len(all_merged)
    return merged


def fmt_dur(ms):
    if ms is None:
        return "N/A"
    s = ms / 1000
    if s >= 3600:
        return f"{s/3600:.1f}h"
    if s >= 60:
        return f"{s/60:.1f}m"
    return f"{s:.1f}s"


def fmt_tok(n):
    if not n:
        return "0"
    return f"{n:,}"


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 opencode_compare_sessions.py <set1> <set2>", file=sys.stderr)
        print("  Each set is comma-separated session IDs: ses1,ses2,ses3 ses4,ses5", file=sys.stderr)
        sys.exit(1)

    set1_ids = [s.strip() for s in sys.argv[1].split(",") if s.strip()]
    set2_ids = [s.strip() for s in sys.argv[2].split(",") if s.strip()]

    if not set1_ids or not set2_ids:
        print("Error: each set must contain at least one session ID", file=sys.stderr)
        sys.exit(1)

    print(f"\nProcessing set 1 ({len(set1_ids)} sessions)...", file=sys.stderr)
    s1 = process_set(set1_ids)
    print(f"Processing set 2 ({len(set2_ids)} sessions)...", file=sys.stderr)
    s2 = process_set(set2_ids)

    if not s1 or not s2:
        print("Error: could not process one or both sets", file=sys.stderr)
        sys.exit(1)

    all_tools = sorted(set(list(s1["tool_times"].keys()) + list(s2["tool_times"].keys())))

    dur1 = s1.get("total_duration", 0)
    dur2 = s2.get("total_duration", 0)

    label1 = f"Set 1 ({s1['session_count']} sess)"
    label2 = f"Set 2 ({s2['session_count']} sess)"

    print(f"\n  {'Metric':<30} {label1:>30} {label2:>30} {'Δ':>15}")
    print(f"  {'─'*30} {'─'*30} {'─'*30} {'─'*15}")
    print(f"  {'Sessions':<30} {', '.join(set1_ids):>30.30s} {', '.join(set2_ids):>30.30s} {'':>15}")
    print(f"  {'Duration':<30} {fmt_dur(dur1):>30} {fmt_dur(dur2):>30} {fmt_dur(dur1 - dur2):>15}")
    print(f"  {'Thinking':<30} {fmt_dur(s1['thinking_time']):>30} {fmt_dur(s2['thinking_time']):>30} {fmt_dur(s1['thinking_time'] - s2['thinking_time']):>15}")

    t1 = s1["session_tokens"]
    t2 = s2["session_tokens"]
    print(f"  {'Tokens input':<30} {fmt_tok(t1.get('input',0)):>30} {fmt_tok(t2.get('input',0)):>30} {fmt_tok(t1.get('input',0) - t2.get('input',0)):>15}")
    print(f"  {'Tokens output':<30} {fmt_tok(t1.get('output',0)):>30} {fmt_tok(t2.get('output',0)):>30} {fmt_tok(t1.get('output',0) - t2.get('output',0)):>15}")
    print(f"  {'Tokens reasoning':<30} {fmt_tok(t1.get('reasoning',0)):>30} {fmt_tok(t2.get('reasoning',0)):>30} {fmt_tok(t1.get('reasoning',0) - t2.get('reasoning',0)):>15}")
    print(f"  {'Total tokens':<30} {fmt_tok(t1.get('input',0) + t1.get('output',0) + t1.get('reasoning',0)):>30} {fmt_tok(t2.get('input',0) + t2.get('output',0) + t2.get('reasoning',0)):>30} {fmt_tok((t1.get('input',0) + t1.get('output',0) + t1.get('reasoning',0)) - (t2.get('input',0) + t2.get('output',0) + t2.get('reasoning',0))):>15}")

    r1 = s1["thinking_time"] + sum(s1["tool_times"].values())
    r2 = s2["thinking_time"] + sum(s2["tool_times"].values())
    if dur1:
        print(f"  {'Model inference (rest)':<30} {fmt_dur(dur1 - r1):>30} {fmt_dur(dur2 - r2) if dur2 else 'N/A':>30} {'':>15}")

    t1["total_tool_calls"] = sum(s1["tool_counts"].values())
    t2["total_tool_calls"] = sum(s2["tool_counts"].values())
    print(f"  {'Total tool calls':<30} {t1['total_tool_calls']:>30,} {t2['total_tool_calls']:>30,} {(t1['total_tool_calls'] - t2['total_tool_calls']):>15,}")

    print()
    print(f"  {'Tool':<16} {'Calls':>8} {'Avg':>10} {'Total':>10}   {'Calls':>8} {'Avg':>10} {'Total':>10}   {'Δ Calls':>8} {'Δ Total':>10}")
    print(f"  {'─'*16} {'─'*8} {'─'*10} {'─'*10}   {'─'*8} {'─'*10} {'─'*10}   {'─'*8} {'─'*10}")
    for tool in sorted(all_tools, key=lambda t: max(s1["tool_counts"].get(t, 0), s2["tool_counts"].get(t, 0)), reverse=True):
        c1, t1_val = s1["tool_counts"].get(tool, 0), s1["tool_times"].get(tool, 0)
        c2, t2_val = s2["tool_counts"].get(tool, 0), s2["tool_times"].get(tool, 0)
        avg1 = t1_val / c1 / 1000 if c1 else 0
        avg2 = t2_val / c2 / 1000 if c2 else 0
        dc = c1 - c2
        dt = t1_val - t2_val
        dc_s = f"+{dc}" if dc > 0 else str(dc) if dc < 0 else "0"
        dt_s = f"+{fmt_dur(dt)}" if dt > 0 else fmt_dur(dt) if dt < 0 else "0"
        print(f"  {tool:<16} {c1:>8} {avg1:>9.3f}s {t1_val/1000:>9.3f}s   {c2:>8} {avg2:>9.3f}s {t2_val/1000:>9.3f}s   {dc_s:>8} {dt_s:>10}")


if __name__ == "__main__":
    main()
