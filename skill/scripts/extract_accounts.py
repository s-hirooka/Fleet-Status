#!/usr/bin/env python3
"""Parse twiauto-format account CSVs and produce a deduplicated handle list.

twiauto CSVs are the export format used by the "twiauto" X(Twitter) automation
tool: one row per account, with columns for the account's X URL/handle, proxy
settings, post templates, and a "used / not used" flag. Column names and quoting
style can vary slightly between files (some use TRUE/FALSE, some use 1/0;
some are fully quoted, some aren't) but csv.DictReader handles that fine as
long as the header row is intact.

Usage:
    python extract_accounts.py --input-dir "G:/Xアカウント調査/twiauto" \
        --out accounts.json

    # only rows currently in active use (flag column == "0")
    python extract_accounts.py --input-dir ./csvs --flag-value 0 --out accounts.json

    # include every row regardless of the used/unused flag
    python extract_accounts.py --input-dir ./csvs --no-filter --out accounts.json
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

DEFAULT_ACCOUNT_COL = "Xアカウント名"
DEFAULT_URL_COL = "Xアカウント(URL)"
DEFAULT_DISPLAY_COL = "Xユーザー名"
DEFAULT_GENRE_COL = "ジャンル名"
DEFAULT_FLAG_COL = "使用しない:TRUE／使用する:FALSE"

HANDLE_RE = re.compile(r"x\.com/([A-Za-z0-9_]+)")


def load_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", help="Folder containing the CSV files")
    ap.add_argument("--files", nargs="*", help="Explicit list of CSV files (alternative to --input-dir)")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument("--account-col", default=DEFAULT_ACCOUNT_COL)
    ap.add_argument("--url-col", default=DEFAULT_URL_COL)
    ap.add_argument("--display-col", default=DEFAULT_DISPLAY_COL)
    ap.add_argument("--genre-col", default=DEFAULT_GENRE_COL)
    ap.add_argument("--flag-col", default=DEFAULT_FLAG_COL,
                     help="Column that marks a row disabled/enabled")
    ap.add_argument("--flag-value", default=None,
                     help='Only keep rows where --flag-col equals this value (e.g. "0" for '
                          '"used"). Omit together with --no-filter to keep every row.')
    ap.add_argument("--no-filter", action="store_true", help="Ignore the flag column entirely")
    args = ap.parse_args()

    if args.files:
        files = args.files
    elif args.input_dir:
        files = sorted(glob.glob(os.path.join(args.input_dir, "*.csv")))
    else:
        print("Specify --input-dir or --files", file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No CSV files found.", file=sys.stderr)
        sys.exit(1)

    uniq = {}
    total_rows = 0
    kept_rows = 0
    for path in files:
        fname = os.path.basename(path)
        try:
            rows = load_rows(path)
        except Exception as e:
            print(f"WARN: could not read {fname}: {e}", file=sys.stderr)
            continue
        for i, row in enumerate(rows):
            total_rows += 1
            if not args.no_filter and args.flag_value is not None:
                val = (row.get(args.flag_col) or "").strip()
                if val != args.flag_value:
                    continue
            name = (row.get(args.account_col) or "").strip()
            url = (row.get(args.url_col) or "").strip()
            display = (row.get(args.display_col) or "").strip()
            genre = (row.get(args.genre_col) or "").strip()

            handle = name
            if not handle and url:
                m = HANDLE_RE.search(url)
                if m:
                    handle = m.group(1)
            if not handle:
                continue

            kept_rows += 1
            uniq.setdefault(handle, []).append({
                "file": fname,
                "row": i + 1,
                "url": url or f"https://x.com/{handle}",
                "display": display,
                "genre": genre,
            })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=1)

    print(f"files scanned: {len(files)}")
    print(f"rows scanned: {total_rows}, rows kept after filter: {kept_rows}")
    print(f"unique handles: {len(uniq)}")
    print(f"written to: {args.out}")


if __name__ == "__main__":
    main()
