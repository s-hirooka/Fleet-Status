"""Merge freshly-scraped Amazon Associates Central tables with the local
DB-derived X post/view/click metrics, and write the combined structure the
dashboard template expects to D:\...\dashboard_data\amazon_data.json.

Inputs (write these first, from what you read off the Associates Central
report pages):
  --monthly-json   a JSON list of {tracking, clicks, orders, orderedSales, referralFee}
                    -- the "トラッキングID" grouped table, 過去30日間 period.
  --daily-all-json a JSON list of {date, clicks, orders, orderedSales, referralFee}
                    -- the "日付" grouped table with トラッキングID:すべて, 過去30日間.
  --period-start / --period-end / --period-label   the period shown on the Amazon page
                    (e.g. "2026-08-03" / "2026-09-01" / "過去30日間")

The per-tracking DAILY breakdown (dailyByTracking, one table per tracking ID) is
NOT re-scraped by this script -- it's expensive (one page load per tracking ID)
and carries over unchanged from the last time it was refreshed, merged from the
existing amazon_data.json. Only the monthly summary and the all-tracking daily
total get refreshed automatically.
"""
import argparse
import datetime
import json
import os
import sqlite3

DATA_DIR = r"D:\path\to\twi-autoV2\dashboard_data"
DB_ROOT = r"D:\path\to\twi-autoV2\twiautoのDataBase"


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def compute_db_metrics(period_start, period_end_exclusive):
    """(accountCount per tracking, X posts/views/clicks per (date,tracking) and
    per tracking overall) from the local backup DBs -- same logic used earlier
    in this project's interactive session."""
    from collections import defaultdict

    account_count = defaultdict(set)
    daily = defaultdict(lambda: {"posts": 0, "views": 0, "clicks": 0})

    systems = [d for d in os.listdir(DB_ROOT) if os.path.isdir(os.path.join(DB_ROOT, d))]
    for sysname in systems:
        dbpath = os.path.join(DB_ROOT, sysname, "database", "data.sqlite")
        if not os.path.exists(dbpath):
            continue
        try:
            con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute("SELECT XAccNode, XAccName, Tracking, NotUsed FROM Config")
            node_to_tracking = {}
            for xaccnode, xaccname, tracking, notused in cur.fetchall():
                tracking = (tracking or "").strip()
                if not tracking:
                    continue
                node_to_tracking[xaccnode] = tracking
                try:
                    active = int(notused) == 0
                except (TypeError, ValueError):
                    active = False
                if active and xaccname:
                    account_count[tracking].add(xaccname)

            cur.execute("PRAGMA table_info(XPostMetricSnapshot)")
            cols = [c[1] for c in cur.fetchall()]
            has_clicks = "DetailClickCount" in cols
            select_cols = "XAccNode, TweetUrl, TweetPostedAt, ViewCount" + (", DetailClickCount" if has_clicks else "")
            cur.execute(f"SELECT {select_cols} FROM XPostMetricSnapshot")
            for row in cur.fetchall():
                xaccnode, tweeturl, postedat, viewcount = row[0], row[1], row[2], row[3]
                clickcount = row[4] if has_clicks else 0
                tracking = node_to_tracking.get(xaccnode)
                if not tracking or not postedat:
                    continue
                if not (period_start <= postedat < period_end_exclusive):
                    continue
                date_str = postedat[:10]
                d = daily[(date_str, tracking)]
                d["posts"] += 1
                d["views"] += (viewcount or 0)
                d["clicks"] += (clickcount or 0)
            con.close()
        except Exception as e:
            print(f"WARN {sysname}: {e}")

    return account_count, daily


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--monthly-json", required=True)
    ap.add_argument("--daily-all-json", required=True)
    ap.add_argument("--period-start", required=True)
    ap.add_argument("--period-end", required=True)
    ap.add_argument("--period-label", default="過去30日間")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "amazon_data.json"))
    args = ap.parse_args()

    with open(args.monthly_json, encoding="utf-8") as f:
        monthly_rows = json.load(f)
    with open(args.daily_all_json, encoding="utf-8") as f:
        daily_all_rows = json.load(f)

    period_end_exclusive = (datetime.date.fromisoformat(args.period_end) + datetime.timedelta(days=1)).isoformat()
    account_count, daily = compute_db_metrics(args.period_start, period_end_exclusive)

    # attach account counts + posts/views/clicks to the monthly per-tracking rows
    for r in monthly_rows:
        t = r["tracking"]
        r["accountCount"] = len(account_count.get(t, set()))
        pm = sum(v["posts"] for (d, tr), v in daily.items() if tr == t)
        r["postsMonth"] = pm

    total_account_count = sum(len(s) for s in account_count.values())

    # attach summed X metrics onto the all-tracking daily rows
    x_by_date = {}
    for (d, t), v in daily.items():
        acc = x_by_date.setdefault(d, {"posts": 0, "views": 0, "clicks": 0})
        acc["posts"] += v["posts"]; acc["views"] += v["views"]; acc["clicks"] += v["clicks"]
    for r in daily_all_rows:
        x = x_by_date.get(r["date"], {"posts": 0, "views": 0, "clicks": 0})
        r["posts"] = x["posts"]; r["xViews"] = x["views"]; r["xClicks"] = x["clicks"]

    # preserve the last known per-tracking daily breakdown + click-support system list
    existing = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            existing = json.load(f)

    out = {
        "rows": monthly_rows,
        "amazonPeriod": {"start": args.period_start, "end": args.period_end, "label": args.period_label},
        "amazonLastUpdated": now_iso(),
        "dailyByTracking": existing.get("dailyByTracking", {}),  # not refreshed here, see docstring
        "accountCountByTracking": {t: len(s) for t, s in account_count.items()},
        "monthlyTotalsByTracking": {r["tracking"]: r for r in monthly_rows},
        "dailyAllTracking": daily_all_rows,
        "totalAccountCount": total_account_count,
        "xClickUnsupportedSystems": existing.get("xClickUnsupportedSystems", []),
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"wrote {args.out}")
    print(f"referralFee sum (daily-all, sanity check): {sum(r['referralFee'] for r in daily_all_rows)}")


if __name__ == "__main__":
    main()
