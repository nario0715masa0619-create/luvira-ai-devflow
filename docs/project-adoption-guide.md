# Luvira AI DevFlow 導入ガイド v1

## この仕組みがすること

各案件リポジトリは、共通の Context Lock 検証を呼び出せます。検証は案件・
リポジトリ・Issue・承認記録・期限・許可アクションの一致だけを確認し、
不一致なら停止します。

共通基盤は案件コードを書き換えず、GitHub App、秘密鍵、Webhook、外部 AI の
認証情報も保持しません。

## 導入の前提

導入は対象案件の Issue と PR で明示的に承認します。案件ごとの権限・保護ルール
はその案件に残り、共通基盤が横断的な書込み権限を持つことはありません。

## 案件側に追加するもの

1. `templates/context-lock.json` を元に `.github/luvira-context-lock.json` を作成する。
2. `.github/workflows/luvira-preflight.yml` から共通ワークフローを呼び出す。
3. 初回は `read` のみで実行結果を確認する。

```yaml
name: Luvira preflight
on: workflow_dispatch
jobs:
  preflight:
    uses: nario0715masa0619-create/luvira-ai-devflow/.github/workflows/luvira-context-lock-preflight.yml@main
    with:
      context_lock_path: .github/luvira-context-lock.json
      requested_action: read
```

`pull_request` や `branch` を許可する導入は、別の承認と案件側の branch protection
を必須にします。`merge`、`deploy`、`admin`、`secret` は v1 で利用できません。

## 承認依頼の共通形式

```
承認すること: <具体的な一つの変更>
影響: <対象案件・権限・外部接続・コスト>
しないこと: <今回明示的に除外する行為>
対象: <owner/repository, Issue, Context Lock の期限>
```

承認後も、Context Lock の検証と案件側の保護ルールを通過しなければ作業は始まりません。

## 共通基盤の自己検証

共通リポジトリでは、正常な Lock、不一致リポジトリ、期限切れ、許可外アクション、
禁止アクションを `node tests/validate-context-lock.test.js` で確認します。これにより
案件を接続せずに fail-closed の動作を検証できます。
