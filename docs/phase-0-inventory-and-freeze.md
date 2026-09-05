# Phase 0 — 現状棚卸しと旧経路の凍結

更新日: 2026-09-05

## 目的

アーキテクチャ再設計 v2 の実装前に、現行資産・権限・実行経路を基準化する。この文書は実行承認ではない。旧経路の機能追加を凍結し、Phase 1 以降はこの基準との差分を明示する。

## 凍結ルール

1. 旧経路（Issue コメントから直接 AI を起動する workflow）には機能追加・権限追加・モデル昇格を行わない。
2. 緊急の安全停止・秘密情報の無効化・脆弱性修正だけは、通常のPR・必須チェック・人間承認を経て許可する。
3. 旧経路の停止は Phase 4 で、新経路の受入試験成功後に実施する。Phase 0 では利用不能化しない。
4. 実装AI・レビューAI・監視者に、GitHub設定、GCP IAM、Secret Managerの値、PRマージの権限を追加しない。
5. 仕様、対象コミット、モデル、予算、再試行条件のいずれかを変更する実行は、新しい承認として扱う。

## 現行資産

| 区分 | 資産 | 現状 | 移行上の扱い |
| --- | --- | --- | --- |
| 入口/実行 | `.github/workflows/run-approved-opencode-task.yml` | Issueコメント `/luvira implement` が直接OpenCode Goを起動 | **旧経路。凍結しPhase 4で停止** |
| 制御 | `orchestrator/main.py` | Webhook検証・候補提示・簡易準備判定。耐久状態の唯一の記録ではない | Phase 1でControl Planeへ拡張 |
| AI接続 | OpenCode Go + Secret Manager `opencode-go-api-key-devflow` | 実装用APIキーを取得 | Broker経由の短期実行資格へ置換 |
| 独立レビュー | `.github/workflows/luvira-ai-reviewer.yml` / Luvira AI Reviewer | `pull_request_target`で信頼済みレビューコードを実行 | 新経路の独立レビュー候補として維持・再検証 |
| 生成物検査 | `reject-generated-artifacts.yml` | 資格情報・キャッシュ等の既知パターンを拒否 | Verification Planeの一部へ統合 |
| 実行時監視 | `reviewer-runtime-monitor.yml` 等 | 実行環境の異常を検査 | 監視は停止・記録専用に限定 |
| GitHub Apps | Luvira AI Reviewer / Luvira AI Worker | レビューと作業を別Appで運用 | Publication Adapterは別の最小権限Appへ分離 |
| GCP | `luvira-ai-control-plane` | Secret Manager、OIDCサービスアカウント、オーケストレーター | Phase 1で役割別SA・短期認証へ分離 |

## 確認済みの構造的リスク

- 単一workflowが認証、秘密情報参照、AI実行、Git書込み、PR作成を扱う。
- Issue本文とコメントが仕様・命令・注記を混在させ、自然言語の矛盾が実行内容を変え得る。
- AI作業領域と認証補助ファイルが近接し、許可された出力差分だけを公開する境界がない。
- 監視、品質、公開の判定根拠が分散し、同じ作業の重複実行と利用枠消費を一元制御できない。

## Phase 1 の開始条件

次のPRは、AIを一切起動せず、以下だけを実装対象にする。

- 不変 `task_id` と仕様スナップショットハッシュ
- 状態機械（DRAFTからAUTHORIZEDまで）
- 人間承認の記録、予算、重複実行拒否
- 監査イベントの保存

受入条件は、テスト依頼が構造化仕様と明示承認なしにAI利用枠を消費しないこと。

## 非対象

このPRはIAM変更、Secret Manager変更、GitHub App変更、AI実行、既存workerの停止を含まない。
