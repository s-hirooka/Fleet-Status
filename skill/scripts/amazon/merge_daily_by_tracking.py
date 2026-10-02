"""Refresh the per-tracking-ID DAILY breakdown (dailyByTracking) in amazon_data.json.

This is the piece merge_amazon_data.py deliberately does NOT re-scrape (it needs one
Associates-Central page load per tracking ID). Run this after you have scraped the
"日付" grouped table once per tracking ID (グループ分け=日付, トラッキングIDフィルタ=その1件,
期間=過去30日間, 100件表示) and written them to a JSON like:

  {
    "yourtag-22": [
      {"date": "2026-09-09", "clicks": 443, "orders": 0, "orderedSales": 0, "referralFee": 0},
      ...
    ],
    ...
  }

  (treat Amazon's "-" cells as 0; the clicks column always has a real value)

It joins those Amazon rows with the local-DB X post/view/click metrics for the same
period (same compute_db_metrics used by merge_amazon_data.py), producing rows shaped
{date, clicks, orders, orderedSales, referralFee, posts, xClicks, xViews} sorted by
date descending, and writes them back into amazon_data.json's dailyByTracking. Nothing
else in amazon_data.json is touched except amazonLastUpdated.
"""
import argparse
import datetime
import json
import os

from merge_amazon_data import compute_db_metrics, now_iso, DATA_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-by-tracking-json", required=True,
                    help="scraped {tracking: [{date,clicks,orders,orderedSales,referralFee}]}")
    ap.add_argument("--period-start", required=True)
    ap.add_argument("--period-end", required=True, help="inclusive last day shown on the Amazon page")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "amazon_data.json"))
    args = ap.parse_args()

    with open(args.daily_by_tracking_json, encoding="utf-8") as f:
        scraped = json.load(f)

    period_end_exclusive = (datetime.date.fromisoformat(args.period_end) + datetime.timedelta(days=1)).isoformat()
    _account_count, daily = compute_db_metrics(args.period_start, period_end_exclusive)

    # index X metrics by (date, tracking)
    x_by = {}
    for (d, t), v in daily.items():
        x_by[(d, t)] = {"posts": v["posts"], "xClicks": v["clicks"], "xViews": v["views"]}

    with open(args.out, encoding="utf-8") as f:
        data = json.load(f)

    new_dbt = {}
    for tracking, rows in scraped.items():
        amz_by_date = {r["date"]: r for r in rows}
        x_dates = {d for (d, t) in x_by if t == tracking}
        all_dates = set(amz_by_date) | x_dates
        merged = []
        for d in sorted(all_dates, reverse=True):
            a = amz_by_date.get(d, {})
            x = x_by.get((d, tracking), {"posts": 0, "xClicks": 0, "xViews": 0})
            merged.append({
                "date": d,
                "clicks": a.get("clicks", 0),
                "orders": a.get("orders", 0),
                "orderedSales": a.get("orderedSales", 0),
                "referralFee": a.get("referralFee", 0),
                "posts": x["posts"],
                "xClicks": x["xClicks"],
                "xViews": x["xViews"],
            })
        new_dbt[tracking] = merged

    data["dailyByTracking"] = new_dbt
    data["amazonLastUpdated"] = now_iso()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"wrote {args.out}")
    for t, rows in new_dbt.items():
        amz_ref = sum(r["referralFee"] for r in rows)
        print(f"  {t}: {len(rows)} rows, referralFee sum {amz_ref}")


if __name__ == "__main__":
    main()
