#!/usr/bin/env python3
"""Query the shadowban-api.yuzurisa.com checker for a list of X(Twitter) handles
and classify each one, with built-in retries for two known sources of flakiness:

1. Transient checker-side errors. The service occasionally returns
   profile.exists == false with an error like "Authorization: Denied by access
   control: ... this account is temporarily locked" or "Could not authenticate
   you" even for perfectly normal accounts -- this looks like the checker's own
   backend session getting rate-limited, not the target account's real status.
   So an exists=false result is re-queried a few times (with a real pause
   between attempts) before it's trusted.

2. Noisy shadowban signals. tests.typeahead and tests.search can each read as
   "banned" on a single request but flip back on the next. A ban is only
   reported as such if it reproduces on every retry.

Output is one JSON object per line (JSONL), written incrementally.

Two modes:
- Full run (no --limit): every handle not yet present in --out gets checked
  once, in order. Resumable -- rerunning with the same --out file picks up
  where an interrupted run left off (already-present handles are skipped).
- Batch run (--limit N): instead of "not yet checked ever", picks the N
  *stalest* handles (never-checked handles first, then whichever have the
  oldest checked_at) and (re)checks just those, appending a fresh line for
  each -- including ones that already have an older line in the file. This
  is meant to be run frequently (e.g. every 30 min via a scheduled task) so
  the whole fleet gets refreshed gradually without hammering the checker API
  all at once, which was triggering mass HTTP 500s when done ~389-at-a-time.
  Downstream consumers (build_dashboard.py) always take the *last* line per
  handle, so duplicate lines for a re-checked handle are fine.

Usage:
    python check_shadowban.py --handles-file accounts.json --out results.jsonl
    python check_shadowban.py --handles-file accounts.json --out results.jsonl --limit 10
    python check_shadowban.py --handles example_handle3 example_handle4 --out results.jsonl
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://shadowban-api.yuzurisa.com:444/{}"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(handle, timeout=20):
    """One raw HTTP call. Returns parsed JSON dict, or raises."""
    req = urllib.request.Request(API.format(handle), headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def fetch_with_backoff(handle, max_attempts=4, base_wait=8):
    """Handles 429s and plain network errors. Not concerned with the app-level
    exists=false flakiness -- that's handled by the caller via repeated calls
    to this function."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fetch(handle)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_attempts:
                time.sleep(base_wait * attempt)
                continue
            raise
        except Exception:
            if attempt < max_attempts:
                time.sleep(5)
                continue
            raise


def classify(handle, samples):
    """samples: list of parsed API responses for the same handle (>=1).
    Returns (judgement, detail) using the last sample for fields that don't
    need reproduction, and requiring reproduction across *all* samples for
    the flaky signals (exists=false, typeahead=false, search=false)."""
    last = samples[-1]
    profile = last.get("profile") or {}
    tests = last.get("tests") or {}

    all_exist_false = all((s.get("profile") or {}).get("exists") is False for s in samples)
    any_exist_true = any((s.get("profile") or {}).get("exists") is True for s in samples)

    if all_exist_false and not any_exist_true:
        err = profile.get("error") or ""
        return ("要目視確認(アカウント消滅/凍結の疑い)",
                f"{len(samples)}回とも profile.exists=false。API側エラー: {err or '(エラーメッセージなし)'}"
                f" ※このAPIは一時的な自己都合エラーでもexists=falseを返すことがあるため、"
                f"ブラウザで https://x.com/{handle} を直接開いて404か「アカウントは凍結されています」かを確認してください。")

    # from here on, treat as existing (at least one sample said so)
    typeahead_vals = [ (s.get("tests") or {}).get("typeahead") for s in samples if (s.get("profile") or {}).get("exists") ]
    search_vals = [ (s.get("tests") or {}).get("search") for s in samples if (s.get("profile") or {}).get("exists") ]

    typeahead_banned = len(typeahead_vals) > 0 and all(v is False for v in typeahead_vals)
    search_banned = len(search_vals) > 0 and all(v is False for v in search_vals)
    # "_error" and "_implied_good" are inconclusive, not a ban signal, and a
    # returned tweet-id string means the tweet WAS found in search (not banned)
    search_partially_bad = any(v is False for v in search_vals) and not search_banned

    ghost = (tests.get("ghost") or {}).get("ban")
    more_replies = (tests.get("more_replies") or {}).get("ban")

    if typeahead_banned and search_banned:
        return ("シャドウバンの疑いが高い",
                f"検索結果・検索候補(サジェスト)の両方が非表示の状態が{len(samples)}回の照会すべてで再現(search=false, typeahead=false)")
    if typeahead_banned:
        return ("シャドウバンの疑いあり",
                f"検索候補(サジェスト)が非表示の状態が{len(samples)}回の照会すべてで再現(typeahead=false)。"
                f"通常検索の結果はブレあり(search={search_vals})")
    if search_banned:
        return ("シャドウバンの疑いあり",
                f"通常検索で非表示の状態が{len(samples)}回の照会すべてで再現(search=false)。typeaheadは{typeahead_vals}")
    if search_partially_bad:
        return ("要再確認(検索結果が不安定)",
                f"検索テストの結果が照会のたびに変化(search={search_vals})。ネットワーク起因のノイズの可能性があるため、時間を置いて再度確認を推奨")

    untestable = []
    if ghost is None:
        untestable.append("ゴーストバン(リプライ非表示)")
    if more_replies is None:
        untestable.append("リプライ格下げ")
    if untestable:
        return ("異常なし(一部項目は判定不能)",
                f"検索・検索候補は正常。{'・'.join(untestable)}はリプライ実績不足のため判定不能")
    return ("異常なし", f"search={search_vals[-1]}, typeahead={typeahead_vals[-1]}, "
                        f"ghost_ban={ghost}, more_replies_ban={more_replies}")


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def check_one(handle, exists_false_retries, suspect_retries, request_delay, verbose):
    samples = []
    try:
        first = fetch_with_backoff(handle)
    except Exception as e:
        return {"handle": handle, "judgement": "取得失敗", "detail": f"APIエラー: {e}",
                "raw": [], "checked_at": now_iso(), "reachable": False}
    samples.append(first)

    profile = first.get("profile") or {}
    tests = first.get("tests") or {}

    if profile.get("exists") is False:
        for _ in range(exists_false_retries):
            time.sleep(request_delay)
            try:
                samples.append(fetch_with_backoff(handle))
            except Exception:
                pass
    elif tests.get("typeahead") is False or tests.get("search") is False:
        for _ in range(suspect_retries):
            time.sleep(request_delay)
            try:
                samples.append(fetch_with_backoff(handle))
            except Exception:
                pass

    judgement, detail = classify(handle, samples)
    if verbose:
        print(f"  -> {judgement}: {detail}", flush=True)
    return {"handle": handle, "judgement": judgement, "detail": detail, "raw": samples,
            "checked_at": now_iso(), "reachable": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--handles-file", help="JSON file mapping handle -> [...] (output of extract_accounts.py), "
                                            "or a JSON list of plain handle strings")
    ap.add_argument("--handles", nargs="*", help="Handles given directly on the command line")
    ap.add_argument("--out", required=True, help="JSONL output path (resumable)")
    ap.add_argument("--delay", type=float, default=1.8, help="Seconds to wait between distinct handles")
    ap.add_argument("--exists-false-retries", type=int, default=3,
                     help="Extra queries to run when profile.exists comes back false")
    ap.add_argument("--suspect-retries", type=int, default=3,
                     help="Extra queries to run when typeahead/search look banned")
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                     help="Stop the run (site presumed unavailable) after this many handles in a "
                          "row couldn't be reached at all")
    ap.add_argument("--out-status", help="Where to write a small status JSON after the run "
                                          "(default: <out> with .status.json instead of its extension)")
    ap.add_argument("--limit", type=int, default=None,
                     help="Only (re)check this many handles this run, picking the stalest ones "
                          "(never-checked first, then oldest checked_at). Omit for a full run "
                          "over every not-yet-checked handle.")
    args = ap.parse_args()

    status_path = args.out_status or (os.path.splitext(args.out)[0] + ".status.json")

    handles = []
    if args.handles_file:
        with open(args.handles_file, encoding="utf-8") as f:
            data = json.load(f)
        handles = list(data.keys()) if isinstance(data, dict) else list(data)
    if args.handles:
        handles.extend(args.handles)
    handles = list(dict.fromkeys(handles))  # dedupe, keep order

    if not handles:
        print("No handles given (use --handles-file or --handles).", file=sys.stderr)
        sys.exit(1)

    done = set()
    last_checked_at = {}
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    h = rec["handle"]
                    done.add(h)
                    last_checked_at[h] = rec.get("checked_at") or ""
                except Exception:
                    pass

    print(f"Total handles: {len(handles)}, already done: {len(done)}", flush=True)

    consecutive_failures = 0
    site_down = False
    remaining_after_stop = []

    if args.limit is not None:
        # Stalest-first: never-checked (checked_at "") sorts before any real
        # ISO timestamp, so those come first; among already-checked handles,
        # oldest checked_at comes first.
        pending = sorted(handles, key=lambda h: last_checked_at.get(h, ""))[: args.limit]
        print(f"Batch mode: checking the {len(pending)} stalest of {len(handles)} handles", flush=True)
    else:
        pending = [h for h in handles if h not in done]

    with open(args.out, "a", encoding="utf-8") as out:
        for i, h in enumerate(pending):
            if site_down:
                remaining_after_stop.append(h)
                continue
            print(f"[{i + 1}/{len(pending)}] {h}", flush=True)
            rec = check_one(h, args.exists_false_retries, args.suspect_retries, args.delay, verbose=True)
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            done.add(h)

            if rec.get("reachable") is False:
                consecutive_failures += 1
                if consecutive_failures >= args.max_consecutive_failures:
                    print(f"!! {consecutive_failures} handles in a row could not be reached -- "
                          f"assuming the checker site is temporarily down. Stopping this run.", flush=True)
                    site_down = True
            else:
                consecutive_failures = 0

            time.sleep(args.delay)

    all_handles = set(handles)
    checked_handles = all_handles & done
    unchecked_handles = sorted(all_handles - done)
    if args.limit is not None:
        # Batch mode: "unchecked" (never-checked-even-once) is expected to be
        # large most of the time -- that's just fleet coverage still catching
        # up, not a problem. Report on the batch itself instead of alarming
        # about the whole fleet.
        status = {
            "finished_at": now_iso(),
            "mode": "batch",
            "batch_limit": args.limit,
            "batch_attempted": len(pending) - len(remaining_after_stop),
            "site_available": not site_down,
            "total_handles": len(handles),
            "ever_checked_count": len(checked_handles),
            "never_checked_count": len(unchecked_handles),
            "message": (
                f"このバッチ中にチェッカーサイトに接続できなくなりました"
                f"({consecutive_failures}件連続失敗)。次回のバッチ実行で続きから再開します。"
                if site_down else
                f"{len(pending) - len(remaining_after_stop)}件をチェックしました"
                f"(全{len(handles)}件中、これまでに一度でもチェック済み: {len(checked_handles)}件)。"
            ),
        }
    else:
        status = {
            "finished_at": now_iso(),
            "mode": "full",
            "site_available": not site_down,
            "total_handles": len(handles),
            "checked_count": len(checked_handles),
            "unchecked_count": len(unchecked_handles),
            "unchecked_handles": unchecked_handles,
            "message": (
                f"シャドウバンチェックサイトに接続できませんでした。数時間おいてから再実行してください。"
                f"（{len(unchecked_handles)}件が未チェックのまま残っています。再実行時は未チェックのアカウントから再開します）"
                if site_down else
                ("全アカウントのチェックが完了しました。" if not unchecked_handles
                 else f"{len(unchecked_handles)}件が未チェックです。再実行してください。")
            ),
        }
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    print(f"status written to: {status_path}", flush=True)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
