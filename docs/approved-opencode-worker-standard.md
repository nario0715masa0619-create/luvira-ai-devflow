# Approved OpenCode Worker Standard v1

## 起動条件

OpenCode Worker は、対象リポジトリの Issue にリポジトリ所有者が次の形式で
コメントした場合にだけ起動する。

```text
/luvira implement
<実装してよい内容>
```

通常の Issue 作成、編集、ラベル付け、第三者コメントでは起動しない。PR上の
コメントも対象外である。起動者はリポジトリ所有者と完全一致しなければならない。

## 実行範囲

- OpenCode Go は GitHub Actions の一時環境でのみ実行する。
- APIキーは GCP Secret Manager から実行時にだけ読み込み、GitHub Secret・成果物・ログへ保存しない。
- 既定モデルは `opencode-go/gpt-5.6-luna` とする。高単価モデルへの自動昇格はしない。
- 実装結果は main へ直接変更せず、PRとして提出する。

## 禁止事項

Worker は GitHub Actions、デプロイ、IAM、Secret Manager、認証設定、保護ルールを変更しない。
マージ・デプロイ・権限変更・秘密情報の作成または表示も行わない。

## 品質ゲート

Worker は関係する決定的テストを実行する。提出したPRは、既存のCI、独立AIレビュー、
人間承認、マージ保護を通過するまでマージされない。
