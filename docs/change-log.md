# Change Log

## 概要

このドキュメントは、Luvira AI DevFlow の運用ルール、ワークフロー、役割分担、承認基準の変更履歴を記録するためのものです。

目的は、
- いつ何を変えたかを追えるようにすること
- なぜ変更したのかを後から確認できるようにすること
- 個別案件の一時対応と、標準基盤への正式反映を区別すること

にあります。

---

## 記録ルール

- 変更は日付単位で記録する
- 変更内容だけでなく、理由も残す
- 一時対応か恒久対応かを明記する
- 関連ドキュメントがあれば併記する

---

## テンプレート

### YYYY-MM-DD

- 種別: 追加 / 変更 / 削除
- 対象: `docs/xxxxx.md`
- 区分: 一時対応 / 恒久対応
- 内容:
  - 
- 理由:
  - 
- 関連Issue / PR:
  - 
- 備考:
  - 

---

## 初期記録

### 2026-06-18

- 種別: 追加
- 対象:
  - `README.md`
  - `docs/workflow-standard.md`
  - `docs/roles-and-responsibilities.md`
  - `docs/approval-rules.md`
  - `docs/project-constitution.md`
- 区分: 恒久対応
- 内容:
  - Luvira AI DevFlow の初期ドキュメント一式を作成した
  - 標準ワークフロー、役割分担、承認ルール、共通憲章を定義した
- 理由:
  - AI開発の標準基盤をGitHub上で管理し、今後の案件に共通適用できる状態を作るため
- 関連Issue / PR:
  - 未設定
- 備考:
  - 今後の運用でルールやワークフローは継続的に見直す

---

## 運用記録

ここからは、初期構築後の継続的な変更・改善を記録します。

### 2026-06-18 (整合性レビュー反映)

- 種別: 変更
- 対象:
  - `README.md`
  - `docs/workflow-standard.md`
  - `docs/roles-and-responsibilities.md`
  - `docs/approval-rules.md`
  - `docs/project-constitution.md`
  - `docs/decision-log.md`
- 区分: 恒久対応
- 内容:
  - README / workflow-standard / roles-and-responsibilities / approval-rules / project-constitution / decision-log / change-log の整合性チェックを実施した
  - 役割分担の表記揺れ(GSPA起動主体、Human/GAIC/Perplexityの境界、承認行為を指す動詞など)を統一した
  - `approval-rules.md` に「新規ライブラリ・SaaS・依存パッケージの追加」カテゴリを新設した(DEC-005)
  - 「永続化構造の変更」「権限管理・権限設計に関わる変更」の用語表記を `approval-rules.md` に統一した(DEC-006)
  - GSPAの節目レビューを原則自動実施とし、Humanの関与をスキップ判断に限定する方針を明文化した(DEC-007)
  - `project-constitution.md` に「禁止事項」と「要承認事項」の関係性を明記した
  - `README.md` にdecision-log.mdを文書構成・最初に読むべきドキュメントへ追加した
  - 個別案件専用の旧ワークフロー文書(`ai-workflow-genspark-antigravity.md`, `luvira-ai-devflow-workflow-standard.md`)を `docs/archive/` へ移動する方針をREADME.mdに明記した(DEC-008)
- 理由:
  - 各ドキュメントが個別に役割定義・承認基準を保持していたことで生じていたズレを解消し、今後の改訂でのドリフトを防ぐため
- 関連Issue / PR:
  - 未設定
- 備考:
  - 判断の詳細な背景・理由は `docs/decision-log.md` のDEC-005〜DEC-008を参照
  - 旧ワークフロー文書2点の実際の移動・削除作業は別タスクとして実施し、完了後に本ログへ追記する
