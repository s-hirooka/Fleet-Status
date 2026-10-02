# Fleet Status ダッシュボード仕様書

2026-10-02 時点

> 公開用に、メールアドレス・ホスト名・ローカルパス・ダッシュボードURL・Xアカウント名・AmazonトラッキングIDを仮の値（`monitor@example.com`、`NAS-HOST`、`D:\path\to\…`、`<ARTIFACT_URL>`、`example_handle1`、`yourtag-22` など）に置き換えています。

| フォルダ | 内容 |
| --- | --- |
| `skill/` | Claude Code スキル `x-shadowban-checker`（SKILL.md とスクリプト一式） |
| `dashboard/template.html` | ダッシュボードのHTMLテンプレート（`build_dashboard.py` がJSONを差し込む） |

## 1. 概要

Fleet Status は、twi-autoV2 の12システム・389アカウントについて「Xアカウントの状態」「ロック/凍結メール」「DBバックアップ」「Amazon売上」の4つを1画面で確認するダッシュボードです。4つのスケジュールタスクがそれぞれ担当データを更新し、そのたびにHTMLを再構築して同じArtifact URLに再公開します。

| 項目 | 内容 |
| --- | --- |
| 公開URL | <ARTIFACT_URL> |
| 公開範囲 | 非公開（所有者のみ） |
| 対象システム | twi-auto, twiAutoAmazonNo1, twiautoAmazonNo02〜No08, twiautoNo2, twiautoRakuNo3, twiautoRakutenNo4（12個） |
| 対象アカウント | 各システムの Config テーブルで NotUsed=0（使用中）のXアカウント。2026-10-02時点で389件 |
| 画面構成 | 4タブ：アカウント状況 / メール / DBバックアップ / Amazon売上 |
| 実行環境 | ローカルPC上の Claude Code デスクトップアプリ（アプリ起動中のみスケジュールタスクが動く） |
| 関連スキル | `x-shadowban-checker`（スクリプト一式を格納） |

## 2. システム構成とデータフロー

各タスクは `D:\path\to\twi-autoV2\dashboard_data\` の自分の担当ファイルだけを更新します。そのあと全員が同じ `build_dashboard.py` で全ファイルから1つのHTMLを組み立て、同じURLに再公開します。

```
データ元                         スケジュールタスク                       dashboard_data のファイル
\\NAS-HOST (稼働DB 12個) ─→ fleet-db-backup (毎日5:00)          ─→ backup_status.json / backup_trackings.json ─┐
シャドウバンAPI (yuzurisa)    ─→ fleet-account-status (30分毎・20件) ─→ results_combined.jsonl / accounts_combined.json ┤
Amazonアソシエイト (Chrome)   ─→ fleet-amazon-sales (毎日6:00)       ─→ amazon_data.json                            ┤
Gmail monitor-account (Chrome)    ─→ fleet-mail-check (毎日7:00)         ─→ mail_data.json / mail_read_status.json      ┘
                                                                                     │
template.html ─→ build_dashboard.py (5つのJSONを差し込み) ─→ dashboard_final.html ─→ Artifactに再公開
```

アカウント抽出はネットワーク共有ではなく、fleet-db-backup が作ったローカルのバックアップDBを読みます。Amazonタブの「X投稿数・表示回数・クリック数」もバックアップDBから集計します。

| スクリプト（`C:\Users\<user>\.claude\skills\x-shadowban-checker\scripts\`） | 役割 | 使うタスク |
| --- | --- | --- |
| backup_databases.py | 共有DBをローカルへバックアップし、トラッキングIDを集計 | fleet-db-backup |
| extract_from_sqlite.py | バックアップDBから NotUsed=0 のアカウントを抽出 | fleet-account-status |
| check_shadowban.py | チェックが古い順に `--limit` 件をAPIで判定し、結果を追記 | fleet-account-status |
| amazon/merge_amazon_data.py | Amazon実績とDBのX投稿指標をマージ | fleet-amazon-sales |
| merge_mail_alerts.py | 新しく見つかったロック/凍結メールを mail_data.json に追加 | fleet-mail-check |
| build_dashboard.py | 全データを template.html に差し込み、dashboard_final.html を出力 | 全タスク |

## 3. データ定義

`build_dashboard.py` は `template.html` の5つのプレースホルダをJSONで置換します。ページ内では次の定数として参照されます。

| 定数 | プレースホルダ | 元ファイル | 主な項目 |
| --- | --- | --- | --- |
| DATA | `__DATA_JSON__` | accounts_combined.json + results_combined.jsonl(.bak) + mail_overrides.json − mail_read_status.json | handle, display, genre, system, judgement, checked_at, detail, url（1アカウント×所属システムごとに1行） |
| STATUS | `__STATUS_JSON__` | DATAから算出 | finishedAt（checked_atの最大値）, siteAvailable, totalHandles, checkedCount, uncheckedCount |
| MAIL | `__MAIL_JSON__` | mail_data.json − mail_read_status.json | account, lastCheckedAt, records[]（handle, display, status, reason, date, gmail_search） |
| BACKUP | `__BACKUP_JSON__` | backup_status.json + backup_trackings.json | system, ok, backedUpAt, bytes, rows, error, trackings[] |
| AMAZON | `__AMAZON_JSON__` | amazon_data.json | rows[]（tracking, clicks, orders, orderedSales, referralFee, accountCount, postsMonth）, amazonPeriod, amazonLastUpdated, dailyByTracking, dailyAllTracking, accountCountByTracking, monthlyTotalsByTracking, totalAccountCount, xClickUnsupportedSystems |

### 判定の合成ルール（DATA）

1. `results_combined.jsonl.bak`（前回の全件結果）を読み、ハンドルごとの初期値にする。
2. `results_combined.jsonl` を先頭から順に重ねる。同じハンドルは最後の行が優先。ただし `reachable=false`（API障害）の行は、既存の判定を上書きしない。
3. `mail_overrides.json` にあるハンドルは「ロック(Gmail確認)」などの判定で固定表示する。
4. `mail_read_status.json` の readHandles（通知メールを既読にしたハンドル）は、手順3の固定表示とメール一覧の両方から除外する。未読に戻せば再表示される。
5. どれにも結果がなければ「未チェック」。accounts_combined.json にないハンドルは「(個別指定・元データ外)」システムとして表示する。

## 4. 画面仕様

ヘッダーに対象アカウント数・システム数・最終チェック状況を表示し、その下に4つのタブを置きます。最後に開いたタブはブラウザの localStorage（`fleetStatusTab`）に記憶します。

| タブ | 表示内容 | 操作 |
| --- | --- | --- |
| アカウント状況 | KPIタイル、判定の内訳バー、アカウント表（Xアカウント / 表示名 / ジャンル / システム名 / 判定 / チェック日時） | キーワード検索、システム絞り込み、KPIタイルでの判定絞り込み、列ソート、「シャドウバンチェック」ボタン |
| メール | monitor@example.com で検知したロック/凍結通知。新しい順に直近10件（日時 / 状態 / ハンドル / 理由）、総件数 | 「更新」ボタン |
| DBバックアップ | システム名 / 最新バックアップ日時 / サイズ(MB) / AmazonトラッキングID / 状態（成功・失敗理由）、成功数と最終実行日時 | 「バックアップする」ボタン |
| Amazon売上 | 月次：トラッキングID別のクリック数 / 注文数 / 注文商品売上 / 紹介料 / Xアカウント数 / 投稿数。日次：日付別にAmazon指標 + X投稿数 / 表示回数 / クリック数 / CTR | 月次・日次切替、トラッキングID選択（個別 or 合計）、「Amazon取得」ボタン |

### 判定ステータス

判定の文字列を上から順に照合し、最初に当てはまった区分で色分けします。

| 区分 | 条件（判定文字列） | 色 |
| --- | --- | --- |
| シャドウバンの疑いが高い | 「高い」を含む | オレンジ |
| シャドウバンの疑いあり | 「シャドウバン」を含む | オレンジ |
| 凍結/消滅 | 「凍結」または「消滅」を含む | 赤 |
| 要再確認/未チェック | 「要」「取得失敗」を含む、「未チェック」、またはどれにも当てはまらない | 黄 |
| 異常なし(一部判定不能) | 「一部項目」を含む | 緑 |
| 異常なし | 「異常なし」と完全一致 | 緑 |

### ボタンの動作

画面上の4つのボタンは処理を直接実行しません。Artifact の共有DB（`db` capability）にリクエスト日時を記録するだけで、実行にはチャットでClaudeに依頼する必要があります。

| ボタン | 記録先ドキュメント | チャットでの依頼文 |
| --- | --- | --- |
| シャドウバンチェック / 再チェックをリクエスト | `state/recheck_request` | 「シャドウバンチェックして」 |
| 更新（メール） | `state/mail_recheck_request` | 「Gmailを再チェックして」 |
| バックアップする | `state/db_backup_request` | 「DBバックアップして」 |
| Amazon取得 | `state/amazon_fetch_request` | 「Amazon売上を取得して」 |

各ドキュメントは `{requestedAt: ISO日時}` のみを持ち、画面には「前回のリクエスト：日時」として表示されます。

## 5. バックアップ仕様

各システムの稼働DBを、共有側に一切書き込まずにSQLite公式のbackup APIでローカルへ複製します。単純なファイルコピーは使いません（過去に12個中3個が「database disk image is malformed」になったため）。

| 項目 | 仕様 |
| --- | --- |
| スクリプト | `backup_databases.py --out "...\dashboard_data\backup_status.json"` |
| コピー元 | `\\NAS-HOST\<システム名>\database\data.sqlite` |
| コピー先 | `D:\path\to\twi-autoV2\twiautoのDataBase\<システム名>\database\data.sqlite` |
| 接続方式 | コピー元をURIフラグ `immutable=1` で開く。SMB共有ではSQLiteのロックが動かず通常の接続は失敗するため。共有側には書き込みを一切行わない |
| 世代管理 | 1世代のみ。実行ごとにコピー先と `-wal` / `-shm` / `-journal` を削除して作り直す |
| 成功判定 | コピー元が存在し、backup API が完了したものを ok=true。コピー元が無い場合は `source not found` で失敗 |
| 出力1 | `backup_status.json`：システムごとに ok, rows, source_bytes, dest_bytes, elapsed_sec, backed_up_at, error |
| 出力2 | `backup_trackings.json`：システムごとのAmazonトラッキングID一覧（バックアップ後のDBから集計） |
| 所要時間 | 12システムで約3分（2026-10-02実績：1システムあたり2.6〜50.9秒、サイズ122〜476MB） |

バックアップ先のDBは、アカウント抽出（`extract_from_sqlite.py`）とAmazonタブのX投稿指標（`XPostMetricSnapshot` テーブル）の読み取り元を兼ねます。

## 6. 運用

4つのスケジュールタスクがそれぞれ自分の担当データだけを更新し、最後に共通手順（HTML再構築→再公開）を実行します。定義は `C:\Users\<user>\.claude\scheduled-tasks\<taskId>\SKILL.md` にあります。

| taskId | 実行タイミング | 更新するデータ | 外部接続 |
| --- | --- | --- | --- |
| fleet-account-status | 30分おき、20件ずつ（389件一巡に約10時間） | accounts_combined.json, results_combined.jsonl | shadowban-api.yuzurisa.com |
| fleet-db-backup | 毎日 5:00（最大約8分の揺らぎあり） | backup_status.json, backup_trackings.json | \\NAS-HOST（読み取りのみ） |
| fleet-amazon-sales | 毎日 6:00（同上） | amazon_data.json | Amazonアソシエイト・セントラル（Chrome） |
| fleet-mail-check | 毎日 7:00（同上） | mail_data.json, mail_read_status.json, mail_overrides.json（解除確認時のみ） | Gmail monitor@example.com（Chrome） |

### 共通の公開手順

1. `python build_dashboard.py` で `dashboard_final.html` を生成する。
2. Artifactツールで公開URLに再公開する（capabilities `{"db": {}}`、アイコンは省略して既存を維持）。
3. 「新しい版が公開済み」で拒否されたら、保存された公開版と生成物を比較する。差分が自タスクのデータ定数だけなら、生成物をそのまま再公開する。各タスクは全データを共通フォルダから組み直すため、他タスクの更新は通常すでに含まれている。

### 安全上の約束

- \\NAS-HOST 側には書き込み・更新・削除を一切行わない。
- Amazon・Gmailでログインが求められたら中断して報告する。パスワードは入力しない。
- ロック/凍結の通知メール自体は既読にしない。既読化は利用者が「対応済み」と示す合図として使う。
- `results_combined.jsonl` はリネーム・削除せず追記し続ける。

### 既知の制約と課題

| 内容 | 影響 |
| --- | --- |
| スケジュールタスクは Claude デスクトップアプリが起動中のときだけ動く | アプリを閉じている間は更新が止まる |
| 画面のボタンはリクエストを記録するだけ | 実行にはチャットでの依頼が必要 |
| Amazon日次のトラッキングID別内訳は自動更新しない | 更新には「Amazon日次を更新して」と依頼する |
| Xクリック数は12システム中5システムのみ計測可能 | 未対応：twiautoAmazonNo03〜No07, twiautoNo2, twiautoRakuNo3 |
| 下位の少額値はAmazon側で非表示（「—」） | 個別トラッキングIDの日次合計が月次と一致しないことがある |
| バックアップは1世代のみ | 壊れたDBをバックアップすると、前日分に戻せない |
| メールタブの見出しに「既読処理済み」と表示される | 通知メールを既読にしない現在の運用と文言が合っていない |
| `backup_databases.py` の2行目で SyntaxWarning（`\P`） | 動作に影響はない。docstringをraw文字列にすれば解消 |
| 4タスクが同じURLに再公開する | 公開が競合で拒否されることがある（手順3で対応） |
