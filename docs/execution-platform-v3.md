# Execution Platform v3 — 実行基盤の再設計

## 決定

既存の `Authorize approved DevFlow task` からWorkerを同期起動する経路を凍結する。GitHub Actionsは人間承認の記録だけを担い、実行開始・再試行・結果回収・失敗分類はControl Planeが管理する永続的な実行レコードへ分離する。

## 解決する失敗

- 承認後に実行対象パスを変更できる。
- 1回の通信・設定・Job起動失敗で承認済みtaskが使い捨てになる。
- 外部APIのHTTP失敗が抽象化され、原因と再試行可能性を判定できない。
- GitHub Actions、Cloud Run、Brokerに状態と権限判断が分散する。

## 不変な承認対象

人間が承認する `TaskSpec` は次をすべて含み、hashとapproval bindingの対象にする。

- repository、base commit、requested action、受入条件、予算、期限
- `execution_scope.allowed_paths`（正規化済みの相対パス許可リスト）
- 実装モデルの選択規則と最大退避回数
- 許可しない操作、公開先、必要な品質ゲート

承認後に上記のどれかを変える場合は、新しいtaskと承認が必要である。

## 永続状態機械

```text
DRAFT → VALIDATED → AWAITING_HUMAN_APPROVAL → AUTHORIZED
  → EXECUTION_QUEUED → EXECUTION_RUNNING
  → ARTIFACT_VERIFIED → REVIEWING → READY_TO_PUBLISH → PUBLISHED
                         ↘ REJECTED

EXECUTION_RUNNING → EXECUTION_FAILED_RETRYABLE → EXECUTION_QUEUED
EXECUTION_RUNNING → EXECUTION_FAILED_FINAL
任意の未公開状態 → CANCELLED | EXPIRED
```

- `AUTHORIZED` は承認済みだが未着手。承認ワークフローはここで終了する。
- `EXECUTION_QUEUED` は一意の実行要求を表し、同じtaskの重複起動を防ぐ。
- `EXECUTION_FAILED_RETRYABLE` は分類済みの一時障害のみ。承認は再利用する。
- `EXECUTION_FAILED_FINAL` は仕様違反、権限不足、検証不合格など。人間の新しい判断が必要。
- 状態遷移はControl Planeだけが比較更新で書き込む。Worker、監視者、GitHub Actionsは直接変更できない。

## 実行レコード

taskとは別に `ExecutionRecord` を永続保存する。

| 項目 | 用途 |
|---|---|
| execution_id | 冪等キー。Job起動・Artifact受領と1対1に結合 |
| task_id / spec_hash | 承認済み仕様へ固定 |
| attempt | 同一承認内の再試行回数 |
| provider / model policy | 選択根拠と利用量監査 |
| external_operation_id | Cloud Run Job等の外部操作を追跡 |
| failure_code | 秘密値を含まない分類済み理由 |
| timestamps / audit events | 状態遷移と操作者の監査証跡 |

外部応答本文、トークン、秘密値、プロンプトは保存しない。HTTP status、provider名、固定エラーコード、request idだけを記録する。

## 責任境界

| コンポーネント | 許可 | 禁止 |
|---|---|---|
| GitHub Action | 人間承認をControl Planeへ記録 | Worker起動、Secret参照、結果解釈 |
| Control Plane | 仕様・承認・状態遷移・実行キュー | モデル実行、PR作成 |
| Execution Broker | キュー済み実行の開始、最小Envelope、外部API失敗の分類 | 承認内容変更、GitHub公開 |
| Isolated Worker | 指定Envelopeの処理、制限成果物生成 | 長期資格情報、GitHub/GCP管理、公開 |
| Verification Plane | 成果物検証、受領の冪等化 | 仕様変更、PR作成 |
| Publication Adapter | 全品質ゲート後のドラフトPR作成 | マージ、権限変更 |

## 実行前診断

実働起動前にBrokerは以下をread-only検査し、結果を `ExecutionPreflight` として保存する。

1. TaskSpec hash、期限、許可パス、予算、状態。
2. BrokerのJob実行・状態参照・専用ログ閲覧権限。
3. Worker Jobの存在、固定イメージ、無権限Workerサービスアカウント。
4. 専用Artifact経路の読み書き境界。
5. Provider・モデルの利用可能性（秘密値やプロンプトを送らない）。

失敗時はJobを起動しない。診断結果だけを返し、修復は通常PRで行う。

## 受入試験（GCP実行前に必須）

CIで偽のCloud Run/Loggingクライアントを使い、次を全件実証する。

1. 同じ承認で通信失敗から再試行でき、二重Jobを作らない。
2. 許可パス・base commit・予算を承認後に変更すると起動不能。
3. 403、404、409、429、5xx、timeoutを分類し、retryable/finalを固定する。
4. 外部APIの詳細本文や秘密値をログ・Firestore・GitHub出力に残さない。
5. Artifact重複受領、別task出力、未許可差分をfail-closedで拒否する。
6. GitHub Actionsの再送は承認記録を冪等にするが、実行を直接起動しない。

この受入試験が通るまで、Cloud Run Jobの実働テストを行わない。

## 移行

1. 既存の同期bootstrap endpointと手動workflowを「旧経路」として停止する。
2. v3のTaskSpec、ExecutionRecord、状態機械、preflightを実装し、CIの偽クライアントで受入試験を通す。
3. 専用のキュー消費者をBrokerに接続する。GitHub Actionからの直接Job起動は復活させない。
4. 非機密bootstrapを一度だけ実働検証する。
5. 検証済み差分・二重レビュー・Publication AdapterをPhase 3として別設計・別PRで接続する。
