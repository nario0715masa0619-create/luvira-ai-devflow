# Phase 2 — 隔離実行と検証

## 最初の実装範囲

Phase 2は、承認済みtaskを直ちにAIへ送らない。最初にExecution Brokerが、資格情報を含まない `WorkerEnvelope` を作成できることを検証する。このEnvelopeには対象リポジトリ、固定ベースコミット、受入条件、予算、変更許可パスだけを含める。

Workerに渡さないものは次の通り。

- GitHub token、GitHub App鍵、checkout資格情報、PR作成権限
- GCPサービスアカウント、Secret Manager、Cloud Run、IAMの権限
- 長期AI provider key
- 公開・マージ・デプロイの権限

Workerが返せるものは、許可パスに限定された差分と検証結果だけである。公開はPhase 3の専用Publication Adapterだけが行う。

## fail-closed条件

- taskが `AUTHORIZED` 以外ならWorker Envelopeを作らない。
- 初回アクションが `implementation` 以外なら作らない。
- 許可パスが空、絶対パス、親ディレクトリ参照、資格情報形式、または `.github/workflows/`、`security/`、`infra/`、`terraform/` を対象に含む場合は拒否する。
- モデル選択、provider呼び出し、Cloud Run Job作成、GitHub書き込みはこの段階では実装しない。

## 次の段階

1. 専用の無権限Workerサービスアカウントと、出力専用の検証Artifact保管先を作成する。
2. Brokerだけが短期入力を渡すCloud Run Jobを起動する。
3. 受領した差分をVerification Planeが許可パス・秘密情報・生成物・テスト結果でfail-closed検査する。
4. Phase 3でのみ、独立レビュー通過済みArtifactをPublication Adapterへ渡す。
