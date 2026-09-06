# 人間承認の運用手順

この手順は、構造化されたIssue FormがControl Planeで `AWAITING_HUMAN_APPROVAL` になった後だけに使う。これは実装AIを起動する手順ではない。承認記録を `AUTHORIZED` に進めるだけであり、Execution Brokerが導入されるまでAI実行、ブランチ作成、PR作成は発生しない。

## 初回設定

GitHubのリポジトリ設定で `human-approval` Environment を作成し、required reviewersを設定する。この保護がない状態では `Authorize approved DevFlow task` workflowを実行してはいけない。

このworkflowはGitHub OIDCから、private Cloud Run Control Planeだけを呼び出す。長期鍵、OpenCode Go APIキー、GitHub App秘密鍵をrunnerや入力欄に置かない。

## 承認する前の確認

1. Issue Formの対象リポジトリ、ベースコミット、予算、受入条件、禁止事項が正しいこと。
2. Control Planeが返した `task_id` と `approval_binding` が対象Issueの値と一致すること。
3. 実装を許可する判断が明確に人間からなされていること。

## 実行

Actionsの `Authorize approved DevFlow task` を手動実行し、`task_id` と `approval_binding` をそのまま入力する。Environmentのレビュー承認後、Control Planeは不変の仕様スナップショットと照合してから `AUTHORIZED` を記録する。

同じtask_idへの再実行、異なるbinding、状態遷移済みのtaskはfail-closedで拒否される。
