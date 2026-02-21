# UT（単体テスト）
- 最終更新日: 2026-02-18
- 対象プロジェクト: `pay_app`

## 1. 対象
- `app/services/scheduler.py`
- `app/services/statement_import.py`
- `app/services/forecast.py`

## 2. 実施観点
1. 日付計算（締日/支払日/月末補正）
2. リボ/分割の月次按分
3. サブスク発生日（monthly/yearly/monthly_interval/weekly_interval）
4. 文字列パース（明細テキスト/CSV）
5. 有効開始日・終了日の境界

## 3. 実行コマンド
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py
```

## 4. 期待結果
- 全テストが `OK`
- 失敗時は不具合管理票へ起票し、原因と再現条件を残す

## 5. 直近実績
- 2026-02-18: 16 tests `OK`

## 6. 実施記録（2026-02-18）
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py
```
- 実行結果:
```text
Ran 16 tests in 0.038s
OK
```
- 判定: 合格

## 7. 実施記録（2026-02-18 / 再実行）
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py
```
- 実行結果:
```text
Ran 16 tests in 0.043s
OK
```
- 判定: 合格

## 8. 実施記録（2026-02-18 / API統合テスト追加後）
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

## 9. 実施記録（2026-02-18 / 有効期間拡張）
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_000_api_integration.py tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py tests/test_subscription_effective_dates.py
```
- 実行結果:
```text
Ran 20 tests in 0.124s
OK
```
- 判定: 合格

## 10. 実施記録（2026-02-18 / 月次明細レポート追加）
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_000_api_integration.py tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py tests/test_subscription_effective_dates.py
```
- 実行結果:
```text
Ran 21 tests in 0.148s
OK
```
- 判定: 合格

## 11. 実施記録（2026-02-18 / 月次明細レポート拡張）
- 実施内容:
  自由に使えるお金表示、収入イベントを含む一覧、支払い方法別店舗割合円グラフを追加
- 実行コマンド:
```powershell
.\.venv\Scripts\python.exe -m unittest tests/test_000_api_integration.py tests/test_statement_import.py tests/test_scheduler_subscription.py tests/test_effective_dates.py tests/test_subscription_effective_dates.py
```
- 実行結果:
```text
Ran 21 tests in 0.166s
OK
```
- 判定: 合格
