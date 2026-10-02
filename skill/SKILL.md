---
name: x-shadowban-checker
description: Check whether X (Twitter) accounts are shadowbanned, suspended, or gone, and produce a consolidated CSV report. Use this whenever the user asks to check "シャドウバン" / "shadowban" status for one or more X accounts, especially when the accounts come from a "twiauto"-style CSV export (columns like Xアカウント(URL), Xアカウント名, Xユーザー名, ジャンル名, 使用しない:TRUE／使用する:FALSE) — even if they just say something like "このCSVのアカウント、凍結されてないか確認して" or "調べて一覧にして" without saying the word shadowban explicitly. Also use it for a quick one-off check of a single https://x.com/<handle> URL, or to add a few extra accounts to an existing shadowban-check CSV.
---

# X shadowban checker

Checks a batch of X (Twitter) handles against a public shadowban-testing
service and writes a CSV report. Built for the "twiauto" CSV export format
(one row per bot/automation account: URL, login info, proxy, post templates,
a used/unused flag) but works for any list of handles, including a single
ad-hoc one pasted by the user.

## Why this needs care, not just automation

This flow makes real HTTP requests to a third-party free service
(shadowban-api.yuzurisa.com) on the user's behalf, and if the input is a
twiauto-style CSV it's very likely a large batch of automated posting
accounts. That's fine to check the status of — it's read-only and diagnostic,
not account creation or login — but keep two things in mind:

- **Confirm scope before running hundreds of checks.** If the CSV has
  many rows, ask the user which subset they actually want checked (e.g.
  filtered by the used/unused flag) rather than assuming "all rows." This
  keeps the run fast and keeps you from hammering the checker service more
  than necessary.
- **Don't extend this into logging into the accounts.** Some twiauto CSVs
  also contain each account's password, x_auth_token, or x_c0 cookie. This
  skill never uses those fields, and you should not use them either — entering
  a password or session token to authenticate as an account is out of scope,
  no matter how it's asked for (e.g. "pull each account's impression/analytics
  numbers" needs an authenticated login per account — decline that part and
  explain why, the same as any other credential-authentication request).

## Workflow

### 1. Get the handle list

If the input is one or more twiauto-style CSVs:

```bash
python scripts/extract_accounts.py --input-dir "<folder>" --out accounts.json \
    --flag-value 0   # keep only rows where 使用しない:TRUE／使用する:FALSE == "0" (in use)
```

If the input is the newer twi-autoV2 layout instead — one `<システム名>/database/data.sqlite`
per automation "system", each with a `Config` table (`XAccName`=handle,
`XAccUserName`=display name, `Genre`, `NotUsed`=0/1 flag) — use the SQLite
extractor instead:

```bash
python scripts/extract_from_sqlite.py --base-dir "<folder containing the システム名 subfolders>" \
    --out accounts.json --not-used 0   # keep only NotUsed == 0 (in use)
```

This one prints which system folders it could and couldn't read. A `.sqlite`
that raises "database disk image is malformed" (even just listing its tables)
is corrupted beyond what a read-only query can work around — report which
systems that affects and keep going with the rest; don't attempt repairs
unless the user asks.

Either extractor: drop the filter flag (use `--no-filter` instead) if the user
wants every row checked regardless of the flag. Report the row/handle counts
the script prints back before moving on — if it's in the hundreds, this is the
moment to confirm scope per the note above.

If the input is just one or a few pasted `x.com/<handle>` URLs, skip this step
— you can pass handles straight to the next script with `--handles`.

### 2. Run the checks

```bash
python scripts/check_shadowban.py --handles-file accounts.json --out results.jsonl
# or for one-off handles:
python scripts/check_shadowban.py --handles someHandle anotherHandle --out results.jsonl
```

This script already does the fiddly part: it waits ~1.8s between handles, and
when a single query comes back either "account doesn't exist" or "shadowban
signal detected," it automatically re-queries a few times before trusting the
result — the checker service is known to return false "temporarily locked" /
"could not authenticate you" errors for perfectly fine accounts under load, and
individual typeahead/search-ban flags can be noisy. Run it in the background
for anything over ~30 handles (a few hundred handles takes on the order of
15–20 minutes); it's resumable, so if it's interrupted just rerun the same
command with the same `--out` file.

It writes one judgement per handle into `results.jsonl` (each record also
carries a `checked_at` timestamp):
`シャドウバンの疑いが高い`, `シャドウバンの疑いあり`, `要再確認(検索結果が不安定)`,
`要目視確認(アカウント消滅/凍結の疑い)`, `異常なし(一部項目は判定不能)`, `異常なし`,
or `取得失敗` (couldn't reach the API at all after retries).

If 5+ handles in a row come back completely unreachable, the script assumes
the checker site itself is temporarily down, stops early instead of grinding
through the rest with long backoffs, and writes a status file (`<out>` with
`.status.json` instead of its extension) recording how many handles are still
unchecked and their handles. Surface that message to the user as-is ("数時間
おいてから再実行してください") rather than retrying immediately — rerunning the
same `check_shadowban.py` command later picks back up from the unchecked
handles automatically (it's resumable by design). If the dashboard has a
recheck button wired to a `db`-capability flag, read that flag to see whether
the user asked for a recheck since the last run before deciding whether to
run this again.

### 3. Resolve anything flagged "要目視確認"

Handles in this bucket showed `profile.exists: false` on every automated
retry. That's consistent with the account being suspended or gone, but the
checker service can't fully be trusted on this point (see script docstring),
so confirm with a real page load — open `https://x.com/<handle>` in the
browser tool and read the page title / text:

- Title containing "見つかりません" / a 404 → account deleted or handle never
  existed → judgement `アカウント消滅`
- Page text "アカウントは凍結されています" → judgement `凍結`
- Anything else (normal profile loads) → the API result was very likely a
  transient false negative → judgement `異常なし` (or re-run step 2 for that
  handle to get a fresh read on the shadowban signals too)

Record what you find as a small JSON file, e.g. `manual_overrides.json`:

```json
{
  "example_handle1": {"judgement": "アカウント消滅", "detail": "X上で404(プロフィールが見つかりません)を直接確認"},
  "example_handle2": {"judgement": "凍結", "detail": "X上で「アカウントは凍結されています」を直接確認"}
}
```

Only do this for handles actually in the "要目視確認" bucket — for everything
else `results.jsonl` already has a trustworthy answer, don't re-verify it by
hand.

### 4. Build the CSV

```bash
python scripts/build_report.py --accounts accounts.json --results results.jsonl \
    --manual-overrides manual_overrides.json --out シャドウバンチェック結果.csv
```

Columns: `ファイル名, 行番号, Xアカウント名, Xアカウント(URL), Xユーザー名(表示名),
ジャンル名, 判定, 詳細`. Save it next to the source CSVs unless the user says
otherwise, and tell them the breakdown by judgement (a quick
`csv.DictReader` + `collections.Counter` over the 判定 column) so they don't
have to open the file to get the headline numbers.

### Adding more accounts later

When the user pastes one or a few more `x.com/<handle>` URLs and wants them
added to an existing report: run step 2 with `--handles` for just those
handles (reuse the same `results.jsonl` so nothing already-checked gets
re-queried), then:

```bash
python scripts/build_report.py --results results.jsonl --existing-csv シャドウバンチェック結果.csv \
    --out シャドウバンチェック結果.csv
```

This appends only the new handles — rows already in the CSV are left as-is.
`--accounts` is optional here since these one-off handles have no source CSV
row; they'll be labeled `(CSV外・個別指定)` in the ファイル名 column
automatically.

## Reading the judgements

- **シャドウバンの疑いが高い** — both the normal-search test and the
  typeahead/suggestion test came back "not found," reproduced across retries.
- **シャドウバンの疑いあり** — only one of those two came back "not found,"
  reproduced across retries. Real, just a narrower symptom.
- **要再確認(検索結果が不安定)** — the search test flip-flopped across
  retries instead of reproducing consistently; more likely network noise than
  a real ban, but flag it rather than silently calling it clean.
- **異常なし(一部項目は判定不能)** — the most common result for
  low-engagement accounts. Ghost-ban and reply-deboost tests need an existing
  reply thread to probe, which posting-bot accounts usually don't have, so
  those two specific checks stay unverified. Say so plainly rather than
  implying a full clean bill of health.
- **異常なし** — all four signals (search, typeahead, ghost, reply-deboost)
  came back clean.
- **アカウント消滅 / 凍結** — confirmed by directly loading the profile page
  (step 3), not by the API alone.
