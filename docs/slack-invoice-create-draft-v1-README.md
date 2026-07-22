# slack-invoice-create-draft-v1 README

対象ワークフロー: [workflows/slack-invoice-create-draft-v1.json](../workflows/slack-invoice-create-draft-v1.json)

本書は、当該n8nワークフローの各ノードが何をしているか、および導入時に必要な設定をまとめたものである。設計上の位置づけ(DevFlow v1における半自律フローの一例、外部送信前段階であること等)は [autonomous-dev-environment-v1.md](autonomous-dev-environment-v1.md) を参照。

## 1. 全体の流れ

```
[/invoice 実行]
    │
    ▼
Webhook - Slash Command ──▶ Verify & Build Modal ──▶ Open Modal(views.open) ──▶ Respond OK - Slash
                                                                                 (Slackにモーダル表示)

[モーダル送信(view_submission)]
    │
    ▼
Webhook - Interactivity ──▶ Verify & Parse Payload ──▶ Validate & Build Invoice ──▶ IF - Is Valid
                                                                                       │
                                        ┌──────────────────────────────────────────────┤
                                        ▼ (Valid)                                      ▼ (Invalid)
                              Respond OK - Interactivity                    Respond Errors - Modal
                                        │                                    (モーダルに項目別エラー表示)
                                        ▼
                              Google Sheets - Append Invoice
                                        │
                        ┌───────────────┴───────────────┐
                        ▼ (成功)                          ▼ (失敗)
                Slack - Post Success                Slack - Post Error
```

Slackの仕様上、`/invoice`実行時(Slash Command)とモーダル送信時(Interactivity)はそれぞれ別のHTTPリクエストとしてSlackから送られてくるため、Webhookエントリーポイントを2つ(Slash Command用・Interactivity用)に分けている。ユーザー要求では「1. Slack Triggerノード」と単一ノードのように書かれているが、Slackの実際の挙動に合わせてこの2系統構成とした。

## 2. ノード一覧と役割

| ノード名 | 種別 | 役割 |
|---|---|---|
| Webhook - Slash Command | Webhook | `/invoice` 実行時にSlackから届くform-urlencodedリクエストを受信 |
| Verify & Build Modal | Code | Slack署名検証(HMAC-SHA256)+ モーダル(Block Kit)のJSON組み立て |
| Open Modal (views.open) | HTTP Request | Slack API `views.open` を呼び、`trigger_id` を使ってモーダルを開く |
| Respond OK - Slash | Respond to Webhook | Slashコマンドへの3秒以内ackを返す(空の200) |
| Webhook - Interactivity | Webhook | モーダルsubmit時にSlackから届く`payload`(url-encoded JSON)を受信 |
| Verify & Parse Payload | Code | Slack署名検証 + `payload`パラメータのJSONパース |
| Validate & Build Invoice | Code | `validate_and_build_invoice`相当。必須項目・amount数値チェック、`invoice_id`生成、Sheets用レコード整形 |
| IF - Is Valid | IF | バリデーション結果で分岐 |
| Respond OK - Interactivity | Respond to Webhook | view_submissionへのack(空の200、モーダルを閉じる) |
| Respond Errors - Modal | Respond to Webhook | バリデーションエラー時、`response_action: errors`形式でモーダルに項目別エラーを表示 |
| Google Sheets - Append Invoice | Google Sheets | `Invoices`シートへ1行追加。ノード自体の`onError: continueErrorOutput`で成功/失敗を分岐 |
| Slack - Post Success | Slack | 成功時、指定チャンネルに完了メッセージを投稿 |
| Slack - Post Error | Slack | Sheets追加失敗時、`error_type: sheets_append_failed`を含むエラーメッセージを投稿 |

## 3. 事前準備(Slack App側)

Slack Appは既存想定のため、以下の設定確認・追加のみでよい。

- **Slash Commands**: `/invoice` のRequest URLを `https://<n8nホスト>/webhook/slack/invoice-command` に設定
- **Interactivity & Shortcuts**: Request URLを `https://<n8nホスト>/webhook/slack/invoice-interactivity` に設定
- **OAuth Scopes(Bot Token)**: `commands`, `chat:write`, `chat:write.public`(またはチャンネル指定にBotを招待)
- **Signing Secret**: n8n側の環境変数 `SLACK_SIGNING_SECRET` に設定(コード内にベタ書きしない)

## 4. n8n側の設定(環境変数・Credentials)

| 名前 | 種別 | 用途 |
|---|---|---|
| `SLACK_SIGNING_SECRET` | 環境変数 | Slackリクエストの署名検証(Code node内で使用) |
| `INVOICE_SHEET_DOCUMENT_ID` | 環境変数 | 請求台帳のGoogle SheetsドキュメントID |
| `INVOICE_SHEET_NAME` | 環境変数(任意, デフォルト`Invoices`) | シート名 |
| `SLACK_NOTIFY_CHANNEL_ID` | 環境変数 | 完了/エラー通知の投稿先チャンネルID |
| Slack credential(`slackApi`) | n8n Credentials | Bot Token。`Open Modal` / `Slack - Post Success` / `Slack - Post Error` で使用 |
| Google Sheets credential(`googleSheetsOAuth2Api`) | n8n Credentials | `Google Sheets - Append Invoice` で使用 |

インポート後、上記CredentialsをそれぞれのノードでREPLACE_WITH_CREDENTIAL_IDの箇所に設定し直す必要がある(エクスポートJSONにはCredential実体は含まれない)。

さらに、n8nインスタンス自体に以下2つの環境変数を設定すること(実機検証で必須と確認済み。8章参照)。

| 環境変数 | 値 | 理由 |
|---|---|---|
| `NODE_FUNCTION_ALLOW_BUILTIN` | `crypto`(または`*`) | Code node内の`require('crypto')`がデフォルトでサンドボックス拒否されるため |
| `N8N_BLOCK_ENV_ACCESS_IN_NODE` | `false` | Code node内の`$env`参照がデフォルトで拒否されるため |

## 5. Google Sheets 台帳仕様

シート名 `Invoices`、カラム順は以下の通り(設計書通り)。

`invoice_id / client_name / amount / currency / subject / issue_date / due_date / status / created_by / created_at`

`documentId` / `sheetName` は環境変数経由で差し替え可能な設計にしている。

## 6. エラーハンドリング方針

- **入力バリデーションエラー**: Slack仕様に沿い、モーダルの`response_action: errors`でフィールド単位のエラーをその場表示する(追加のSlack通知は行わない)
- **Sheets追加失敗**: `error_type: sheets_append_failed` を付与し、Slackチャンネルにエラーメッセージを投稿する
- 将来の拡張(DevFlowのエラー階層: 外部送信失敗・AI失敗等)に備え、エラーオブジェクトに`error_type`フィールドを持たせる構造をv1から採用している

## 7. v1の非対応事項(再掲)

- メール送信(Gmail等)
- 会計ソフト連携(freee/マネーフォワード等)
- PDF生成・送付
- AIによる文面生成・GAIC相当の詳細設計

## 8. 実機検証結果(n8n v2.31.4, ローカル環境)

Slack本体・実Googleアカウントには接続できないため、ローカルにn8n(v2.31.4)を実際にインストールして起動し、Slackが送るリクエストを模擬した署名付きHTTPリクエストで、Webhook到達からGoogle Sheetsノードの分岐までを実機で検証した。手順・詳細ログは [autonomous-dev-environment-v1-review-notes.md](autonomous-dev-environment-v1-review-notes.md) ではなく、本README内の本節に集約する。検証に使ったスクリプトは [scripts/sign_and_send.js](../scripts/sign_and_send.js)(署名付きリクエスト送信)と [scripts/unit_test_code_nodes.js](../scripts/unit_test_code_nodes.js)(Code node単体テスト、`node scripts/unit_test_code_nodes.js`で実行可能)。

### 見つかった不具合(修正済み)

1. **`$json.rawBody`は存在しない。生ボディは`binary.data.data`(base64)に入る**
   Webhookノードの`options.rawBody: true`は、生ボディを`$json.rawBody`ではなく`item.binary.data.data`(base64エンコード)として格納する(n8n-nodes-base v2以降の実装、`Webhook.node.js`で確認)。
2. **bare `$binary`はCode node内で未定義になる**
   n8n v2.31.4のCode nodeはJS Task Runner上で動作し、コードを静的解析(`built-ins-parser.js`)して実際に使われているビルトイン変数だけをランナーに転送する。この解析は`$json` `$input` `$env` `$node` `$execution` `$prevNode`等は認識するが**`$binary`単体の参照は認識しない**ため、`$binary`は常に`undefined`になる。
   → **修正**: `$input.first().binary.data.data`のように`$input`経由でアイテムを取得し、その`.binary`プロパティを読む。`$input`使用はビルトイン解析で認識されるため、bodyのbinaryデータも正しく転送される。
   この2点を反映し、`workflows/slack-invoice-create-draft-v1.json`内の`Verify & Build Modal` / `Verify & Parse Payload`両Code nodeのコードを修正済み。
3. **ワークフローJSONのトップレベルに`id`フィールドが必須**(CLIインポート時に`SQLITE_CONSTRAINT: NOT NULL constraint failed: workflow_entity.id`で失敗したため追加)。`"id": "slack-invoice-create-draft-v1"`を追加済み。

### 確認できたデプロイ時必須設定(コード上の不具合ではなく、n8n運用設定として必須)

- `NODE_FUNCTION_ALLOW_BUILTIN=crypto`(またはワイルドカード`*`)を設定しないと、Code node内の`require('crypto')`がサンドボックスに拒否される
- `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`を設定しないと、Code node内の`$env`参照が`ExpressionError: access to env vars denied`で拒否される
- これらは本ワークフロー固有ではなく、n8nインスタンス側の環境変数設定として本番導入時に必須。[autonomous-dev-environment-v1-review-notes.md](autonomous-dev-environment-v1-review-notes.md)のAntigravity引き継ぎ作業に追加済み

### 実機で確認できたこと(修正適用後、すべて実際のHTTPレスポンス・n8n実行ログで確認)

| 検証項目 | 結果 |
|---|---|
| n8nへのワークフローJSONインポート | 成功(要`id`フィールド追加、上記参照) |
| 各ノードの`typeVersion`(webhook v2 / code v2 / if v2 / httpRequest v4.2 / respondToWebhook v1.1 / googleSheets v4.5 / slack v2.2) | n8n v2.31.4で警告なくインポート・実行可能 |
| Slash Commandがn8n webhookに到達するか | 到達確認(`Webhook - Slash Command`ノードで受信) |
| 署名検証が通るか(正しい署名) | 通過確認(`Verify & Build Modal`成功) |
| 署名検証が正しく拒否するか(偽の署名) | 拒否確認(`Invalid Slack signature`で例外、後続ノードは実行されない) |
| `/invoice`単体でmodal_viewが組み立てられるか | 成功(`views.open`呼び出しの直前まで到達。実際のモーダル表示は本物のSlack Bot Tokenが必要なため未検証、後述) |
| モーダルsubmitがinteractivity webhookに届くか | 到達確認 |
| `validate_and_build_invoice`ロジック(必須項目・数値チェック・invoice_id生成) | 正常入力/異常入力の両方で意図通りの結果(単体テスト11件全通過、実機実行でも同結果を確認) |
| バリデーションエラー時のSlackモーダル項目別エラー表示 | 実際のHTTPレスポンスで`{"response_action":"errors","errors":{...}}`形式を確認 |
| Google Sheetsノードへの到達・`onError: continueErrorOutput`分岐 | 到達確認。ダミーのDocument ID・未設定Credentialのため追加自体は失敗するが、エラー出力側(`Slack - Post Error`)へ正しく分岐することを確認 |
| Slackへの成功/エラー通知呼び出し | ノードには正しく到達するが、本物のSlack Bot Tokenがないため`slack.com`への実送信は未検証(Credential未設定によるエラーで停止) |

### まだ検証できていないこと(実際のSlack App・Google Sheetsアカウントが必要)

- 実際のSlackワークスペースで`/invoice`を実行した際に、モーダルが実際に画面表示されるか(`views.open`が本物のBot Token・本物の`trigger_id`で成功するか)
- 実際のGoogle Sheetsスプレッドシートへの行追加(本物のOAuth Credential・実在するDocument IDが必要)
- 実際のSlackチャンネルへの完了/エラーメッセージ投稿
- 実運用のネットワーク遅延下で、Slash Command実行から`views.open`・ack返却までが3秒以内に収まるか

### 新たに見つかった挙動上の注意点(バグではないが要認識)

- Webhookノードが`responseMode: responseNode`の場合、`Respond to Webhook`ノードに到達する前にワークフローがエラーで停止しても(例: 署名検証失敗)、HTTPレスポンスとしては200・空ボディがSlack側に返る。つまり内部的な失敗はHTTPレスポンスからは見えず、n8nの実行ログ側でしか判別できない。v1では実運用上問題にならないが、v2以降でエラー監視(n8nのエラーワークフロー連携等)を検討する際の前提として記録しておく。

参考(調査時点の情報):
- [Slack Trigger | Nodes | n8n Docs](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.slacktrigger)
- [Validating slack slash command request - n8n Community](https://community.n8n.io/t/validating-slack-slash-command-request/95857)
- [Enable modules in Code node | n8n Docs](https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/configuration-examples/enable-modules-in-code-node)
