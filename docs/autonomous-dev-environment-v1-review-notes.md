# autonomous-dev-environment-v1.md レビューノート

本ノートは [docs/autonomous-dev-environment-v1.md](autonomous-dev-environment-v1.md)(正本)を、正式リポジトリへ反映する前に確認・整理しておくための作業メモである。正本の内容自体はここでは繰り返さず、反映・突合・引き継ぎに必要な論点のみをまとめる。

## 1. GitHubの正式repoに移す際の前提条件

- `luvira-ai-devflow` の実体リポジトリの所在(ローカル/GitHub上のURL)を先に確定させる。現時点では未確認のため、本書は暫定ルート `D:\ClaudeCodeWork\luvira-ai-devflow\` 上で作成している
- 正式repo側に `docs/workflow-standard.md` 等の想定参照先ファイルが実在するか確認し、実在しない場合は本書の参照リンクがリンク切れにならないよう扱いを決める(先に本書を反映するか、参照先を先に揃えるか)
- 正式repo側の `docs/` 配下のディレクトリ構成・命名規則が、本書のファイル名 `autonomous-dev-environment-v1.md` とその想定参照(相対リンク)にそのまま整合するか確認する
- 正式repoのブランチ運用・PRルールに従い、本書の反映もレビュー対象の変更として扱う(直接pushではなくPR経由を前提とする)
- 正式repo側に既存の用語集・命名規則があれば、反映前に本書の用語(「司令塔」「承認ゲート」「半自律」等)との整合を取る

## 2. 確認が必要な既存ファイル一覧

以下は正本内で「想定参照」として扱っているファイル。実体未確認のため、正式repo反映前に内容を確認する必要がある。

- `README.md`
- `docs/workflow-standard.md`
- `docs/roles-and-responsibilities.md`
- `docs/approval-rules.md`
- `docs/project-constitution.md`
- `docs/decision-log.md`
- `docs/change-log.md`

## 3. README に追加すべきリンク案

正本の付記に記載した提案を、反映作業用に再掲・整理する。

- 「ドキュメント一覧」相当のセクションがあれば、既存のdocsリンクと並べて `docs/autonomous-dev-environment-v1.md` へのリンクを追加
- プロジェクト概要・アーキテクチャ説明の直後に「自律開発環境の全体設計はこちらを参照」という誘導リンクを追加
- 「PoC / 現在進行中の取り組み」に相当するセクションがあれば、正本8章(Slack Invoice PoC)への直接リンクを追加

対応要否は `README.md` の実際の構成を確認したうえで判断する。

## 4. workflow-standard.md / roles-and-responsibilities.md / approval-rules.md と突合すべき観点

| 対象ファイル | 突合観点 |
|---|---|
| `workflow-standard.md` | 正本7章(Standard Workflow)の骨格が、実際のワークフロー標準の処理順序・分岐と矛盾しないか。用語(タスク種別、完了処理等)が一致しているか |
| `roles-and-responsibilities.md` | 正本5章(Tool Responsibilities)の表が、既存のRACI定義と粒度・責任範囲がずれていないか。特にn8n/Claude Code/Antigravityの境界表現が一致しているか |
| `approval-rules.md` | 正本6章(Approval Gates)で挙げた5カテゴリが、既存の承認基準・閾値と過不足なく対応しているか。カテゴリの粒度(本書は概念レベル、approval-rules.mdは基準レベル)がずれていないか |

いずれも、正本側は「ゲートが存在する位置・概念」を示すに留め、詳細基準は各既存ファイルを正とする設計になっている。突合作業では、この上位/下位の役割分担が崩れていないかを重点確認する。

## 5. Antigravityに引き継ぐ作業一覧

- 正式リポジトリ `luvira-ai-devflow` の所在確認・特定
- 本書2ファイル(正本 + 本レビューノート)を正式repoの `docs/` 配下へ反映(PR作成)
- `README.md` の実内容確認と、上記3節のリンク案の反映要否判断・実施
- `workflow-standard.md` / `roles-and-responsibilities.md` / `approval-rules.md` / `project-constitution.md` の内容取得と、上記4節・正本付記(既存ドキュメントとの矛盾・要確認事項)に基づく突合作業
- 突合の結果、用語・粒度のズレが見つかった場合の修正提案の作成(既存ファイルの直接編集は行わず、提案として起票する)
- 反映完了後、`decision-log.md` への記録要否の判断(本書の位置づけ自体が設計判断に該当するため記録候補)
