"""Back up each system's live data.sqlite from the \\NAS-HOST network share into
D:\path\to\twi-autoV2\twiautoのDataBase\<system>\database\ using SQLite's own
online Backup API (Connection.backup), NOT a raw file copy -- a raw copy of a live,
possibly WAL-mode database risks exactly the kind of corruption seen before (3 of 12
DBs came back "database disk image is malformed" after a naive copy).

Source is opened with the `immutable=1` URI flag. This is required because SQLite's
normal file locking does not work reliably over SMB network shares (confirmed here:
a plain connect() to the UNC path fails with "unable to open database file" even
though the file is readable at the OS level) -- immutable=1 tells SQLite to skip
locking entirely and treat the file as a stable read source, which is exactly what a
backup pass needs. The source connection is opened read-only in spirit (no write
statement is ever issued against it) and Connection.backup() only ever reads from
its source -- so nothing is created, modified, or deleted on the \\NAS-HOST side.

The destination file (and any stale -wal/-shm/-journal sidecars) is removed and
recreated fresh on every run; the destination side is fully disposable per the task.
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

SOURCE_ROOT = r"\\NAS-HOST"
DEST_ROOT = r"D:\path\to\twi-autoV2\twiautoのDataBase"

SYSTEMS = [
    "twi-auto", "twiAutoAmazonNo1", "twiautoAmazonNo02", "twiautoAmazonNo03",
    "twiautoAmazonNo04", "twiautoAmazonNo05", "twiautoAmazonNo06", "twiautoAmazonNo07",
    "twiautoAmazonNo08", "twiautoNo2", "twiautoRakuNo3", "twiautoRakutenNo4",
]


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def backup_one(system):
    src = os.path.join(SOURCE_ROOT, system, "database", "data.sqlite")
    dst_dir = os.path.join(DEST_ROOT, system, "database")
    dst = os.path.join(dst_dir, "data.sqlite")

    if not os.path.exists(src):
        return {"system": system, "ok": False, "error": f"source not found: {src}"}

    src_size = os.path.getsize(src)
    os.makedirs(dst_dir, exist_ok=True)
    for ext in ("", "-wal", "-shm", "-journal"):
        p = dst + ext
        if os.path.exists(p):
            os.remove(p)

    t0 = time.time()
    src_con = None
    dst_con = None
    try:
        src_con = sqlite3.connect(f"file:{src}?immutable=1", uri=True)
        # sanity read before committing to a full page-by-page backup
        src_con.execute("SELECT COUNT(*) FROM Config").fetchone()

        dst_con = sqlite3.connect(dst)
        src_con.backup(dst_con)
        dst_con.close()
        src_con.close()

        # verify the freshly-written backup is actually valid
        check_con = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        row_count = check_con.execute("SELECT COUNT(*) FROM Config").fetchone()[0]
        check_con.close()

        elapsed = time.time() - t0
        return {
            "system": system, "ok": True, "rows": row_count,
            "source_bytes": src_size, "dest_bytes": os.path.getsize(dst),
            "elapsed_sec": round(elapsed, 1), "backed_up_at": now_iso(),
        }
    except Exception as e:
        try:
            if src_con:
                src_con.close()
        except Exception:
            pass
        try:
            if dst_con:
                dst_con.close()
        except Exception:
            pass
        return {"system": system, "ok": False, "error": str(e)}


def compute_trackings(systems, dest_root=DEST_ROOT):
    """Distinct Amazon tracking IDs per system, read from the just-refreshed LOCAL
    backup copies (not the network share) -- used for the dashboard's DB backup tab."""
    out = {}
    for system in systems:
        dbpath = os.path.join(dest_root, system, "database", "data.sqlite")
        if not os.path.exists(dbpath):
            continue
        try:
            con = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute('SELECT DISTINCT Tracking FROM Config WHERE Tracking IS NOT NULL AND TRIM(Tracking) != ""')
            out[system] = sorted(set(r[0].strip() for r in cur.fetchall() if r[0] and r[0].strip()))
            con.close()
        except Exception:
            out[system] = []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "backup_status.json"))
    ap.add_argument("--trackings-out", default=None,
                     help="Where to write the per-system Amazon tracking ID list (default: alongside --out, as backup_trackings.json)")
    ap.add_argument("--systems", nargs="*", default=SYSTEMS)
    args = ap.parse_args()

    trackings_out = args.trackings_out or os.path.join(os.path.dirname(args.out), "backup_trackings.json")

    results = {}
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                results = json.load(f)
        except Exception:
            results = {}

    for i, system in enumerate(args.systems):
        print(f"[{i+1}/{len(args.systems)}] {system} ...", flush=True)
        rec = backup_one(system)
        results[system] = rec
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        if rec["ok"]:
            mb = rec["dest_bytes"] / 1e6
            print(f"  OK  {mb:.1f}MB  {rec['elapsed_sec']}s  rows={rec['rows']}", flush=True)
        else:
            print(f"  FAIL: {rec['error']}", flush=True)

    ok_count = sum(1 for r in results.values() if r.get("ok"))
    print(f"DONE: {ok_count}/{len(args.systems)} succeeded", flush=True)

    trackings = compute_trackings(args.systems)
    with open(trackings_out, "w", encoding="utf-8") as f:
        json.dump(trackings, f, ensure_ascii=False, indent=1)
    print(f"trackings written to: {trackings_out}", flush=True)


if __name__ == "__main__":
    main()
