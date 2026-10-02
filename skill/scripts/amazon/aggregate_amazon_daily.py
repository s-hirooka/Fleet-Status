import sqlite3, os, json
from collections import defaultdict

BASE = r"D:\path\to\twi-autoV2\twiautoのDataBase"
SYSTEMS = [d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d))]

# match the Amazon report window exactly: Aug 03 2026 - Sep 01 2026 (inclusive)
RANGE_START, RANGE_END = "2026-08-03", "2026-09-02"  # [start, end)

# (date, tracking) -> post count ; tracking -> account set (for reference)
daily = defaultdict(int)
accounts_by_tracking = defaultdict(set)

for sysname in SYSTEMS:
    dbpath = os.path.join(BASE, sysname, "database", "data.sqlite")
    if not os.path.exists(dbpath):
        continue
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
            accounts_by_tracking[tracking].add(xaccname)

    try:
        cur.execute("SELECT XAccNode, TweetUrl, TweetPostedAt FROM XPostMetricSnapshot")
        seen = set()
        for xaccnode, tweeturl, postedat in cur.fetchall():
            tracking = node_to_tracking.get(xaccnode)
            if not tracking or not postedat:
                continue
            if not (RANGE_START <= postedat < RANGE_END):
                continue
            date_str = postedat[:10]
            key = (date_str, tracking, tweeturl)
            if key in seen:
                continue
            seen.add(key)
            daily[(date_str, tracking)] += 1
    except Exception as e:
        print(f"WARN {sysname}: {e}")
    con.close()

out = []
for (date_str, tracking), count in daily.items():
    out.append({"date": date_str, "tracking": tracking, "posts": count})
out.sort(key=lambda r: (r["date"], r["tracking"]), reverse=True)

with open(os.path.join(os.path.dirname(__file__), "amazon_daily_agg.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print(f"rows: {len(out)}")
for r in out[:20]:
    print(r)
