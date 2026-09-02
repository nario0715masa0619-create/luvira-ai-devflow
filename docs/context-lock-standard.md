# Luvira Context Lock Standard v1

## 目的

Context Lock は、AI や自動化が作業を始める前に、**どの案件の、どの
リポジトリで、何をしてよいか**を機械的に照合する共通契約です。
人間が AI 間で指示をコピーして文脈をつなぐ運用を前提にしません。

この標準は LUVIRA-SEC-001 に従います。秘密鍵、外部 Bot、Webhook、
本番操作は v1 の対象外です。

## 正本と適用範囲

- 正本の作業依頼は、対象案件リポジトリの GitHub Issue とする。
- 各案件は、このリポジトリの共通ワークフローを明示的に呼び出して opt-in
  する。採用前に自動化は開始しない。
- Context Lock は対象案件リポジトリに JSON として保存する。ブランチ、PR、
  承認記録から参照できるため、個別 AI の会話履歴を正本にしない。

## 最小スキーマ

```json
{
  "version": 1,
  "projectId": "example-product",
  "repository": "owner/example-product",
  "issue": 123,
  "approvalUrl": "https://github.com/owner/example-product/issues/123#issuecomment-1",
  "expiresAt": "2026-12-31T00:00:00Z",
  "allowedActions": ["read", "plan", "comment"],
  "protectedBranch": "main"
}
```

`projectId` は組織内で一意にする。`repository` は実行したリポジトリと完全
一致しなければならない。`approvalUrl` は人間が判断した記録を指し、実装者 AI
自身の自己承認を指してはならない。

## Fail-closed 条件

次のいずれかで、ワークフローは停止し、変更・PR作成・外部送信を行いません。

- ファイルがない、JSON が壊れている、必須項目がない
- `repository`、Issue 番号、または要求アクションが一致しない
- 承認 URL が対象リポジトリの Issue を指さない
- 有効期限切れ
- 許可アクション外の要求

## アクション区分

| 区分 | v1 の扱い |
| --- | --- |
| `read` / `plan` / `comment` | Context Lock が有効な場合のみ許可候補 |
| `branch` / `pull_request` | 案件側の別PR・人間承認・保護ルールが必要 |
| `merge` / `deploy` / `admin` / `secret` | v1 では常に不許可 |

## 人間の役割

人間は AI 間の中継をせず、承認依頼の要約だけを確認します。承認依頼には少なく
とも「承認すること」「影響」「しないこと」「対象 Issue/リポジトリ/期限」を
含めます。GitHub の正式レビューが必要な案件では、それも別途保護ルールに従い
ます。

Context Lock の自己検証が成功しても、それだけでは変更を完了扱いにしません。
独立クロスレビューと READ-ONLY 監視の要件は
[`independent-review-monitor-standard.md`](independent-review-monitor-standard.md) を正とします。

## 例外と更新

期限延長、許可範囲の拡張、対象リポジトリの変更は新しい承認記録を必要とします。
不一致または認証情報の所在不明は LUVIRA-SEC-001 に従い、失効または再発行が
確認されるまで停止します。
