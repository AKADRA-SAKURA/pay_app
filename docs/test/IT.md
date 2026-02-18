# IT（結合テスト）
- 最終更新日: 2026-02-18
- 対象プロジェクト: `pay_app`

## 1. 目的
API・DB・スケジューラ連携で、月次請求/予測に整合性があることを確認する。

## 2. 主要シナリオ
1. 口座/予定/サブスク登録 → イベント再作成 → 予測へ反映
2. カード明細登録（手入力/CSV/テキスト）→ 請求額へ反映
3. リボ/分割登録 → 請求月で加算
4. クレカチャージ登録 → カード請求側で計上
5. カード有効期間外の明細が請求対象外になる
6. 円グラフAPI（カード店舗・年間コスト）が期待値を返す

## 3. 確認観点
- `cashflow_events` の `source` と金額符号
- `card_statements.amount_yen` と内訳合計一致
- 予測合計と口座別合計の整合
- UI表示値とAPI返却値の一致

## 4. 実施手順（例）
1. 初期データを用意
2. `/events/rebuild` 実行
3. `/api/forecast/accounts`, `/api/forecast/free` を確認
4. カード関連登録後に再度 `/events/rebuild` を実行
5. `/api/cards/merchant-pie` を確認

## 5. 合格基準
- 主要シナリオで不整合なし
- 重大不具合なし

## 6. 実施記録（2026-02-18）
- 実施方法: `sqlite:///:memory:` の一時DBで、`app.models` + `app.services.scheduler` を結合して検証
- シナリオ1（カード請求内訳加算）:
  `CardTransaction(10000)` + カード払い`Subscription(2000)` + `CardRevolving(3000)` + `CardInstallment(3000)` が請求に合算されることを確認
- シナリオ2（有効期間境界）:
  カード有効開始日を請求期間後にした場合、当該期間の明細が `0` 計上になることを確認
- 実行結果:
```text
IT-SC1 PASS: statement=18000, event=-18000, occurrences=1
IT-SC2 PASS: statement=0, event=0
IT RESULT: PASS (2 scenarios)
```
- 補足: `rebuild_events` 実行時に SQLAlchemy の `SAWarning`（Identity map 入替）を1件検知したが、検証結果自体は `PASS`
- 当時未実施: APIエンドポイント経由IT（`/events/rebuild`, `/api/forecast/*`）は `app/main.py:115` の `SyntaxError` で起動不可のため保留

## 7. 実施記録（2026-02-18 / API経由IT）
- 実施方法:
  `fastapi.testclient.TestClient` + `dependency_overrides` で `get_db` を共有インメモリDB（`sqlite://` + `StaticPool`）に差し替えて検証
- 事前対応:
  `app/main.py` の構文エラーを修正して起動可能化
- シナリオ:
  1. `POST /events/rebuild` が `303` を返す  
  2. `GET /api/forecast/accounts` が `200` かつ `accounts` / `total_series` を返す  
  3. `GET /api/forecast/free` が `200` かつ `series` を返す  
  4. `GET /api/cards/merchant-pie` が `200` で集計合計が期待値以上  
  5. `POST /oneoff/import-text`（新機能）で単発支払いが作成される（`amount=-1234`）
- 実行結果:
```text
IT-API PASS: /events/rebuild=303, /api/forecast/accounts=200, /api/forecast/free=200, /api/cards/merchant-pie=200
IT-API PASS: /oneoff/import-text=303, created amount=-1234
```
- 判定: 合格

## 8. 実施記録（2026-02-18 / 自動化）
- 追加テスト: `tests/test_000_api_integration.py`
- 自動化した観点:
  1. `/events/rebuild` 実行可否
  2. `/api/forecast/accounts` / `/api/forecast/free` のレスポンス整合
  3. `/api/cards/merchant-pie` の集計反映
  4. `/oneoff/import-text` による単発支払い登録
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_000_api_integration.py tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py
```
- 実行結果:
```text
Ran 18 tests in 0.150s
OK
```
- 判定: 合格
