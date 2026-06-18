# Change Log

## 運用記録

### 2026-06-18 — DevFlow v0.2 整合性整理と外部SaaS導入モード追加

#### 変更概要

- `docs/workflow-standard.md` を更新し、判断レイヤーの整理、承認動詞の統一、Perplexity → GSPA のエスカレーション順序の明文化、差戻し対応の独立フェーズ化を反映した。
- `docs/workflow-standard.md` に「外部SaaS導入モード」を追加し、GUI依存タスクを標準ワークフローの正式対象として定義した。
- `docs/workflow-standard.md` において Railway 導入タスクの完了条件を「Public Domain の公開URL取得」に統一した。Railway の公開URLは Settings の Networking から Generate Domain により取得できる。[cite:395][cite:407]
- `docs/roles-and-responsibilities.md` を更新し、Human / Perplexity / GSPA / GAIC / Claude / n8n / Antigravity の責務境界を再整理した。
- `docs/roles-and-responsibilities.md` に Browser Automation Agent を追加し、GUI依存タスクの実行担当として定義した。
- `docs/roles-and-responsibilities.md` に Human の専用操作として、ログイン、2FA、CAPTCHA、決済、法的同意を追加した。Human-in-the-loop 方式では、人間がライブセッションで介入後に自動化を再開できる。[cite:396][cite:398][cite:403]
- `README.md` を更新し、役割表を `docs/roles-and-responsibilities.md` の全体像表と同期した。
- `README.md` の「最初に読むべきドキュメント」と「ドキュメント構成」に `docs/decision-log.md` を追加した。
- `README.md` に、`docs/archive/` を旧設計書・案件依存ドキュメントの退避先として扱う方針を明記した。
- `README.md` に、外部SaaS導入は n8n とブラウザ自動化エージェントを組み合わせて扱う標準運用であることを追記した。Browser Use は n8n から HTTP 統合または community node で利用できる。[cite:373][cite:370][cite:361]

#### 背景

- 役割分担の表現揺れ、承認対象の分散管理、Perplexity / GSPA / Human の境界の曖昧さを解消する必要があった。
- Railway のような GUI 主体の初期設定作業や、今後の外部SaaS導入を個別テンプレート量産で処理すると、保守コストが増大する懸念があった。
- そのため、個別SaaS別テンプレートではなく、Human-in-the-Loop 前提の共通ブラウザ自動化レイヤーを標準フローとして採用した。[cite:396][cite:414][cite:419]

#### 方針

- 承認や差戻しに関する論点整理は、まず Perplexity が担当する。
- 設計レベルの懸念が残る場合のみ GSPA へエスカレーションする。
- GUI依存タスクは n8n をハブとして実行し、Human 専用操作だけを人間介入点として停止させる。
- Railway 導入では、公開URLの取得時点で完了とし、アプリ応答確認は別タスクに切り分ける。[cite:395][cite:396][cite:373]

#### 関連判断

- DEC-005: 承認カテゴリへの「新規ライブラリ・SaaS・依存パッケージの追加」新設。
- DEC-006: 用語統一（永続化構造 / 権限管理・権限設計 など）。
- DEC-007: GSPA 起動は節目で原則自動実施、Human はスキップ判断のみ。
- DEC-008: 旧ドキュメントは `docs/archive/` へ退避する方針。
- DEC-009: 外部SaaS導入は Human-in-the-Loop の共通ブラウザ自動化レイヤーを標準採用する。
- DEC-010: Railway 導入タスクの完了条件は Public Domain の公開URL取得とする。[cite:395][cite:407]

#### 追記事項

- `ai-workflow-genspark-antigravity.md` と `luvira-ai-devflow-workflow-standard.md` の実体移動または削除は未実施。方針のみ明記済み。
- `docs/archive/` の作成と旧ファイル退避を実施した場合は、本 change-log に追記する。
