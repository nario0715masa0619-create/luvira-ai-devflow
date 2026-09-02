# Luvira DevFlow Orchestrator

Cloud Run上でGitHubイベントを受け、対象リポジトリとIssueをfail-closedで検証する。

## 実行契約

1. GitHubイベントはPub/Sub形式で `/events` へ届く。
2. `repository` が共通基盤の許可値と一致しなければ拒否する。
3. Issue番号と許可済みイベントだけを Context Lock 検証待ちにする。
4. Context Lock、独立AIレビュー、監視、保護ルールを満たすまで、コード変更・マージ・デプロイへ進まない。

## 権限分離

- `devflow-orchestrator`: Pub/Sub発行、監査ログのみ。
- `devflow-deployer`: Cloud Runデプロイのみ。
- `ai-reviewer-oidc`: Vertex AIレビューのみ。

各IDは別OIDC Providerまたは別サービスアカウントで分離する。
