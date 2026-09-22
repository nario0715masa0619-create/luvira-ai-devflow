# READY製品のOrca実装受付

`READY` 台帳に登録済みの製品への実装依頼は、**Orca Product DevFlow Intake v1** を使う。
Webhookは司令塔 `luvira-ai-devflow` に `ai-approval` Issueを一件だけ作る。Issue本文の
`Project ID` と `Repository` は、署名済みGitHub webhookでControl Planeが台帳照合する。

台帳が `READY` ではない、または台帳とリポジトリが一致しない場合、Control Planeは
承認タスクを作らずに拒否する。n8nはWorker起動、モデル呼出し、PR作成を行わない。

Orcaの受付スクリプトは、起動プロセスに注入済みの既存受付URLから同じn8n Cloud
ホストを使い、製品専用のWebhookパスを選ぶ。追加のWindows環境変数やレジストリへ
受付キーを保存しない。
