# Orca新規プロダクト受付

新規プロダクトの依頼は、既存プロダクトの実装受付に送ってはならない。Orcaは新規プロダクト専用のn8n入口へ送信し、司令塔リポジトリ `luvira-ai-devflow` に `project-onboarding` ラベル付きの承認Issueを一件だけ作成する。

この段階で作られるものは承認Issueだけである。リポジトリ作成、Worker実行、OpenCode利用、実装PRの作成は一切しない。

承認後は `Authorize project onboarding` が `project-provisioning` 保護環境を通して、非公開リポジトリ作成・初期化・GitHub App接続確認を行う。台帳が `READY` になるまでは、通常の実装受付は必ず拒否する。

## n8n側の受付契約

ワークフロー名は **Orca Project Onboarding Intake v1** とする。入力は `owner`、`slug`、`description` だけであり、受付先のGitHubリポジトリは常に `nario0715masa0619-create/luvira-ai-devflow` に固定する。入力値で受付先を切り替えてはならない。

正常時の応答は `status: AWAITING_HUMAN_APPROVAL`、`project_id`、`issue_number`、`issue_url` を含める。n8nのHTTP認証とOrca側の `DEVFLOW_ORCA_N8N_INTAKE_KEY` は、既存の実装受付キーと同じ値を使ってよいが、Webhook URLは必ず別にする。
