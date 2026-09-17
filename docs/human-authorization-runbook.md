# 人間承認の運用手順

この手順は、構造化されたIssue FormがControl Planeで `AWAITING_HUMAN_APPROVAL` になった後だけに使う。これは実装AIを起動する手順ではない。承認記録を `AUTHORIZED` に進めるだけであり、Execution Brokerが導入されるまでAI実行、ブランチ作成、PR作成は発生しない。

## 初回設定

GitHubのリポジトリ設定で `human-approval` Environment を作成し、required reviewersを設定する。この保護がない状態では `Authorize approved DevFlow task` workflowを実行してはいけない。

このworkflowは専用GitHub OIDC providerと専用サービスアカウントから、private Cloud Run Control Planeだけを呼び出す。専用サービスアカウントには、対象のControl Planeサービスにだけ `roles/run.invoker` と `roles/run.viewer` を与える。`run.viewer` は固定URLを保持せずに現在のサービスURLを読むためだけに必要であり、Cloud Runデプロイ、Secret Manager、GitHub App、AI providerへの権限は与えない。長期鍵、OpenCode Go APIキー、GitHub App秘密鍵をrunnerや入力欄に置かない。

OIDCは二つの専用IDに分離する。検証ジョブは、対象リポジトリとworkflow名だけを固定した `github-human-approval-discovery` / `devflow-approval-check` を使い、Control Planeの受理済み確認だけを行う。保護承認後の記録ジョブだけが、リポジトリ・workflow名・`human-approval` Environmentをすべて固定した `github-human-approval` / `devflow-human-approval` を使う。両方とも対象サービスに限った `roles/run.invoker` と `roles/run.viewer` だけを持つ。

対応する契約は `security/workload-identity/github-human-approval.json` に保存し、PRでは `Verify human approval identity` がworkflowとの不整合をfail-closedで止める。検証専用IDへデプロイ、Secret Manager、GitHub App、AI providerの権限を追加してはならない。

## 承認する前の確認

`Authorize approved DevFlow task` は、保護承認を要求する前に Issue Form
が Control Plane で受理済みかを自動確認する。ここで失敗した場合は、GitHub
環境の承認を行わず、Issue の `ai-approval` ラベル、必須項目、出力許可パス、
参照許可パスを修正してから再実行する。

1. Issue Formの対象リポジトリ、ベースコミット、予算、受入条件、禁止事項が正しいこと。
2. Control Planeが返した `task_id` と `approval_binding` が対象Issueの値と一致すること。
3. 実装を許可する判断が明確に人間からなされていること。

## 実行

Actionsの `Authorize approved DevFlow task` を手動実行し、`task_id` と `approval_binding` をそのまま入力する。Environmentのレビュー承認後、Control Planeは不変の仕様スナップショットと照合してから `AUTHORIZED` を記録する。

同じtask_idへの再実行、異なるbinding、状態遷移済みのtaskはfail-closedで拒否される。
