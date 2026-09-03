# Luvira DevFlow Orchestrator

Cloud Run上でGitHubイベントを受け、対象リポジトリとIssueをfail-closedで検証する。

## 実行契約

1. GitHubイベントはPub/Sub形式で `/events` へ届く。
2. GitHub App/Webhookイベントは `/github/webhook` へ届く。公開入口は署名検証を必須とし、署名不一致・未設定時はfail-closedで拒否する。
3. `repository` が共通基盤の許可値と一致しなければ拒否する。
4. Issue番号と許可済みイベントだけを Context Lock 検証待ちにする。
5. Context Lock、独立AIレビュー、監視、保護ルールを満たすまで、コード変更・マージ・デプロイへ進まない。
6. `/readiness/opencode-go` は、Secret Managerから実行中だけ渡されるキーでOpenCode Goのモデル一覧に接続する。キー、モデル名、プロンプトは応答・ログへ出さず、接続可否と件数だけを返す。コード生成は行わない。

## 権限分離

- `devflow-orchestrator`: Pub/Sub発行、監査ログのみ。
- `devflow-deployer`: Cloud Runデプロイのみ。
- `ai-reviewer-oidc`: Vertex AIレビューのみ。

各IDは別OIDC Providerまたは別サービスアカウントで分離する。
