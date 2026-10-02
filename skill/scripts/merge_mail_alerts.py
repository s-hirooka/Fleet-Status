"""Merge newly-found Gmail lock/suspend records into the persistent mail-alerts
dataset the dashboard reads (D:\...\dashboard_data\mail_data.json).

Write the NEW records you found this run (only the ones not already present --
dedupe by handle+date+status, since re-scanning "in:anywhere" with no date
filter will re-surface old threads too) to a JSON file shaped like:
  [{"handle": "...", "display": "...", "status": "ロック"|"凍結"|"凍結(異議却下)",
    "reason": "...", "date": "2026-09-02T10:00:00+09:00", "gmail_search": "..."}]
then run this script to append them (existing records are kept; total is not
capped here -- the dashboard's own JS keeps only the most recent 10 visible).
"""
import argparse
import datetime
import json
import os

DATA_DIR = r"D:\path\to\twi-autoV2\dashboard_data"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new-records-json", required=True, help="JSON list of newly-found records (can be empty list)")
    ap.add_argument("--account", default="monitor@example.com")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "mail_data.json"))
    args = ap.parse_args()

    with open(args.new_records_json, encoding="utf-8") as f:
        new_records = json.load(f)

    existing = {"account": args.account, "lastCheckedAt": None, "records": []}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)

    seen = {(r["handle"], r["date"], r["status"]) for r in existing.get("records", [])}
    added = 0
    for r in new_records:
        key = (r["handle"], r["date"], r["status"])
        if key not in seen:
            existing["records"].append(r)
            seen.add(key)
            added += 1

    existing["account"] = args.account
    existing["lastCheckedAt"] = now_iso()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False)

    print(f"added {added} new record(s), total {len(existing['records'])}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
