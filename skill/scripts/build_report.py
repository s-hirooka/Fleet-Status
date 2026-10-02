#!/usr/bin/env python3
"""Merge account metadata + shadowban check results into the final CSV report.

Auto-detects which source the accounts came from by looking at the metadata
shape:
  - CSV origin (extract_accounts.py): entries have file/row -> columns
    ファイル名, 行番号, Xアカウント名, Xアカウント(URL), Xユーザー名(表示名), ジャンル名, 判定, 詳細, チェック日時
  - SQLite origin (extract_from_sqlite.py): entries have system -> columns
    Xアカウント, 表示名, ジャンル, システム名, 判定, チェック日時, 詳細, Xアカウント(URL)

Three ways to feed it data for a given handle, in priority order:
  1. --manual-overrides JSON: {"handle": {"judgement": "...", "detail": "..."}}
     Use this for handles Claude confirmed by hand in the browser (the
     "要目視確認" bucket from check_shadowban.py).
  2. --results JSONL: output of check_shadowban.py (has "judgement"/"detail"/"checked_at").
  3. Handles with no matching result at all get 判定="未チェック".

Handles not present in --accounts at all -- e.g. one-off URLs the user asked
about directly -- are still included if they appear in --results or
--manual-overrides, tagged as ad-hoc.

Usage:
    python build_report.py --accounts accounts.json --results results.jsonl --out report.csv

    # add a handful of one-off accounts to an already-built report
    python build_report.py --results results.jsonl --existing-csv report.csv --out report.csv
"""
import argparse
import csv
import json

ADHOC_LABEL = "(個別指定・元データ外)"

CSV_SCHEMA_FIELDS = ["ファイル名", "行番号", "Xアカウント名", "Xアカウント(URL)",
                      "Xユーザー名(表示名)", "ジャンル名", "判定", "詳細", "チェック日時"]
DB_SCHEMA_FIELDS = ["Xアカウント", "表示名", "ジャンル", "システム名", "判定", "チェック日時",
                     "詳細", "Xアカウント(URL)"]


def load_jsonl(path):
    out = {}
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out[rec["handle"]] = rec
    return out


def detect_schema(meta):
    for entries in meta.values():
        for e in entries:
            if "system" in e:
                return "db"
            if "file" in e:
                return "csv"
    return "db"  # default for ad-hoc-only runs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accounts", help="JSON from extract_accounts.py or extract_from_sqlite.py")
    ap.add_argument("--results", help="JSONL from check_shadowban.py")
    ap.add_argument("--manual-overrides", help="JSON: {handle: {judgement, detail}}")
    ap.add_argument("--existing-csv", help="Prior report to keep and append/update rows in")
    ap.add_argument("--schema", choices=["csv", "db"], help="Force a column schema instead of auto-detecting")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    meta = {}
    if args.accounts:
        with open(args.accounts, encoding="utf-8") as f:
            meta = json.load(f)

    schema = args.schema or detect_schema(meta)
    fieldnames = CSV_SCHEMA_FIELDS if schema == "csv" else DB_SCHEMA_FIELDS
    key_col = "Xアカウント名" if schema == "csv" else "Xアカウント"

    results = load_jsonl(args.results)

    overrides = {}
    if args.manual_overrides:
        with open(args.manual_overrides, encoding="utf-8") as f:
            overrides = json.load(f)

    rows = []
    if args.existing_csv:
        with open(args.existing_csv, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        existing_handles = {r[key_col] for r in rows}
    else:
        existing_handles = set()

    all_handles = set(meta.keys()) | set(results.keys()) | set(overrides.keys())
    new_handles = [h for h in all_handles if h not in existing_handles]

    for handle in sorted(new_handles):
        judgement, detail, checked_at = "未チェック", "", ""
        if handle in overrides:
            judgement = overrides[handle].get("judgement", judgement)
            detail = overrides[handle].get("detail", detail)
            checked_at = overrides[handle].get("checked_at", checked_at)
        elif handle in results:
            judgement = results[handle].get("judgement", judgement)
            detail = results[handle].get("detail", detail)
            checked_at = results[handle].get("checked_at", checked_at)

        entries = meta.get(handle)
        if not entries:
            entries = [{"url": f"https://x.com/{handle}", "display": "", "genre": "",
                        "file": ADHOC_LABEL, "row": "", "system": ADHOC_LABEL}]

        for e in entries:
            if schema == "csv":
                rows.append({
                    "ファイル名": e.get("file", ADHOC_LABEL),
                    "行番号": e.get("row", ""),
                    "Xアカウント名": handle,
                    "Xアカウント(URL)": e.get("url", f"https://x.com/{handle}"),
                    "Xユーザー名(表示名)": e.get("display", ""),
                    "ジャンル名": e.get("genre", ""),
                    "判定": judgement,
                    "詳細": detail,
                    "チェック日時": checked_at,
                })
            else:
                rows.append({
                    "Xアカウント": handle,
                    "表示名": e.get("display", ""),
                    "ジャンル": e.get("genre", ""),
                    "システム名": e.get("system", ADHOC_LABEL),
                    "判定": judgement,
                    "チェック日時": checked_at,
                    "詳細": detail,
                    "Xアカウント(URL)": e.get("url", f"https://x.com/{handle}"),
                })

    sort_key = (lambda r: (r["ファイル名"] != ADHOC_LABEL and r["ファイル名"] or "￿", str(r["行番号"] or ""))) \
        if schema == "csv" else \
        (lambda r: (r["システム名"] != ADHOC_LABEL and r["システム名"] or "￿", r["Xアカウント"]))
    rows.sort(key=sort_key)

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"schema: {schema}")
    print(f"wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
