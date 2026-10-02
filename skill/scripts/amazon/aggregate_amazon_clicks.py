import sqlite3, os, json
from collections import defaultdict

BASE = r"D:\path\to\twi-autoV2\twiautoのDataBase"
SYSTEMS = [d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d))]

RANGE_START, RANGE_END = "2026-08-03", "2026-09-02"  # [start, end)

# (date, tracking) -> {"posts": int, "clicks": int}
daily = defaultdict(lambda: {"posts": 0, "clicks": 0})
click_supported_systems = []
click_unsupported_systems = []

for sysname in SYSTEMS:
    dbpath = os.path.join(BASE, sysname, "database", "data.sqlite")
    if not os.path.exists(dbpath):
        continue
    con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("SELECT XAccNode, Tracking FROM Config")
    node_to_tracking = {}
    for xaccnode, tracking in cur.fetchall():
        tracking = (tracking or "").strip()
        if tracking:
            node_to_tracking[xaccnode] = tracking

    cur.execute("PRAGMA table_info(XPostMetricSnapshot)")
    cols = [c[1] for c in cur.fetchall()]
    has_clicks = "DetailClickCount" in cols
    (click_supported_systems if has_clicks else click_unsupported_systems).append(sysname)

    select_cols = "XAccNode, TweetUrl, TweetPostedAt" + (", DetailClickCount" if has_clicks else "")
    cur.execute(f"SELECT {select_cols} FROM XPostMetricSnapshot")
    for row in cur.fetchall():
        xaccnode, tweeturl, postedat = row[0], row[1], row[2]
        clickcount = row[3] if has_clicks else 0
        tracking = node_to_tracking.get(xaccnode)
        if not tracking or not postedat:
            continue
        if not (RANGE_START <= postedat < RANGE_END):
            continue
        date_str = postedat[:10]
        key = (date_str, tracking)
        daily[key]["posts"] += 1
        daily[key]["clicks"] += (clickcount or 0)
    con.close()

out = []
for (date_str, tracking), v in daily.items():
    out.append({"date": date_str, "tracking": tracking, "posts": v["posts"], "clicks": v["clicks"]})
out.sort(key=lambda r: (r["date"], r["tracking"]), reverse=True)

with open(os.path.join(os.path.dirname(__file__), "amazon_daily_clicks.json"), "w", encoding="utf-8") as f:
    json.dump({
        "rows": out,
        "clickSupportedSystems": click_supported_systems,
        "clickUnsupportedSystems": click_unsupported_systems,
    }, f, ensure_ascii=False, indent=1)

print(f"rows: {len(out)}")
print("click-supported systems:", click_supported_systems)
print("click-UNsupported systems:", click_unsupported_systems)
total_clicks = sum(r["clicks"] for r in out)
total_posts = sum(r["posts"] for r in out)
print("total posts:", total_posts, "total X clicks:", total_clicks)
