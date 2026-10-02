#!/usr/bin/env python3
"""Pull X account handles out of twiauto-V2's SQLite databases (the successor
to the old twiauto CSV export). Layout is one system per subfolder:

    <base>/<システム名>/database/data.sqlite   (table: Config)

Config columns of interest: XAccNode (profile URL), XAccName (the bare
handle -- this is what gets shadowban-checked), XAccUserName (display name),
Genre, NotUsed (0/1 flag).

Usage:
    python extract_from_sqlite.py --base-dir "D:/.../twiautoのDataBase" \
        --out accounts.json --not-used 0

    # every row regardless of the flag
    python extract_from_sqlite.py --base-dir ./db_root --out accounts.json --no-filter
"""
import argparse
import json
import os
import re
import sqlite3
import sys

HANDLE_RE = re.compile(r"x\.com/([A-Za-z0-9_]+)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-dir", required=True, help="Folder containing one subfolder per system")
    ap.add_argument("--db-relpath", default=os.path.join("database", "data.sqlite"),
                     help="Path to the sqlite file, relative to each system folder")
    ap.add_argument("--table", default="Config")
    ap.add_argument("--url-col", default="XAccNode")
    ap.add_argument("--handle-col", default="XAccName")
    ap.add_argument("--display-col", default="XAccUserName")
    ap.add_argument("--genre-col", default="Genre")
    ap.add_argument("--flag-col", default="NotUsed")
    ap.add_argument("--not-used", type=int, default=None,
                     help="Only keep rows where NotUsed equals this (0 = in use). Omit + --no-filter for all rows.")
    ap.add_argument("--no-filter", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    systems = sorted(
        d for d in os.listdir(args.base_dir)
        if os.path.isdir(os.path.join(args.base_dir, d))
    )
    if not systems:
        print(f"No subfolders found under {args.base_dir}", file=sys.stderr)
        sys.exit(1)

    uniq = {}
    total_rows = 0
    kept_rows = 0
    ok_systems = []
    broken_systems = []

    for sysname in systems:
        dbpath = os.path.join(args.base_dir, sysname, args.db_relpath)
        if not os.path.exists(dbpath):
            broken_systems.append((sysname, "data.sqlite not found"))
            continue
        try:
            con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute(
                f'SELECT "{args.url_col}", "{args.handle_col}", "{args.display_col}", '
                f'"{args.genre_col}", "{args.flag_col}" FROM "{args.table}"'
            )
            rows = cur.fetchall()
            con.close()
        except Exception as e:
            broken_systems.append((sysname, str(e)))
            continue

        ok_systems.append(sysname)
        for url, handle_raw, display, genre, flag in rows:
            total_rows += 1
            if not args.no_filter and args.not_used is not None:
                try:
                    flag_val = int(flag)
                except (TypeError, ValueError):
                    flag_val = flag
                if flag_val != args.not_used:
                    continue

            handle = (handle_raw or "").strip()
            url = (url or "").strip()
            if not handle and url:
                m = HANDLE_RE.search(url)
                if m:
                    handle = m.group(1)
            if not handle:
                continue

            kept_rows += 1
            uniq.setdefault(handle, []).append({
                "system": sysname,
                "url": url or f"https://x.com/{handle}",
                "display": (display or "").strip(),
                "genre": (genre or "").strip(),
            })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=1)

    print(f"systems scanned: {len(systems)} (ok: {len(ok_systems)}, unreadable: {len(broken_systems)})")
    if broken_systems:
        print("UNREADABLE / CORRUPTED databases (skipped -- these need attention separately):")
        for name, err in broken_systems:
            print(f"  - {name}: {err}")
    print(f"rows scanned: {total_rows}, rows kept after filter: {kept_rows}")
    print(f"unique handles: {len(uniq)}")
    print(f"written to: {args.out}")


if __name__ == "__main__":
    main()
