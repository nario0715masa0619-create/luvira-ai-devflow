# analyze-repo-for-reuse ワークフロー導入資料

## 概要

本ワークフローは、リポジトリ内の `README.md` と `docs/**/*.md` を収集し、LLM に渡して「リポジトリ概要・再利用候補・注意点」を JSON 形式で出力する n8n ワークフローです。各Markdownは1ファイルずつ取得し、全取得が完了してから1回だけ分析します。出力 JSON は将来の RAG（Retrieval-Augmented Generation）連携に利用できる構造です。

## 収集対象

- リポジトリルートの `README.md`
- `docs/` ディレクトリ配下の `.md` のみ
- `.md` 以外のファイルは収集・送信しません

`Filter Markdown` ノードは、対象パスの配列を1件ずつのn8nアイテムへ展開します。これにより、`Fetch Content` は各パスを独立して取得します。取得件数が一致しない場合は、欠けた文書のまま分析せず失敗します。

## LLM への入力設計

### プロンプト構成
1. **System prompt**: 分析の観点と出力スキーマを定義
2. **User prompt**: 収集したマークダウン群をファイルパス付きで連結

### 出力 JSON スキーマ
- `repository_summary`: リポジトリ全体の概要（1-3文）
- `reuse_candidates`: 再利用に値する知見・モジュール・設計
  - `title`, `description`, `source_files`, `reusability_score` (1-5), `notes`
- `cautions`: 流用時の注意点・制約・前提条件
  - `category`, `description`, `source_files`
- `metadata`: 分析メタデータ（日時、ファイル数、パス一覧）

## RAG 連携を見据えた保存設計

### 構造化方針
出力 JSON は `luvira.repo-analysis.v1` スキーマを採用し、以下の構造で保存します。

```json
{
  "schema_version": "luvira.repo-analysis.v1",
  "repository": "owner/repo",
  "source_ref": "main",
  "source_tree_sha": "<GitHub tree SHA>",
  "generated_at": "2026-01-01T00:00:00Z",
  "summary": { ... },
  "reuse_candidates": [ ... ],
  "cautions": [ ... ],
  "chunks": [
    {
      "chunk_id": "docs-approval-rules-001",
      "source_path": "docs/approval-rules.md",
      "section_header": "承認が必要な変更",
      "content": "...",
      "token_count": 120,
      "embedding_ready": true
    }
  ]
}
```

### 保存形態
- **出力の受け渡し**: n8n の「Convert to File」ノードで JSON 化し、実行ごとにファイルとして出力します。標準定義の `Save File` はn8n実行環境の一時領域へ保存します。長期保管が必要な場合は、運用環境に合わせて GitHub Actions Artifacts、Google Drive、S3 等の保存ノードへ差し替えてください。
- **チャンク単位の分解**: 長文ドキュメントをセクション・ヘッダー単位で `chunks` に分解して保存します。ベクトル DB（Pinecone / Weaviate / pgvector）への取り込み時に、`content` を埋め込み対象テキスト、`metadata` に `source_path` や `chunk_id` を格納することで、出典追跡可能な検索を実現します。
- **メタデータの充実**: ファイルパス、見出し、行番号、更新日時を付与し、RAG の再現性を確保します。
- **スキーマ固定**: `schema_version` を宣言し、後続のインデクサーがスキーマ変更を検知できるようにします。

## 使い方

1. ワークフロー `workflows/analyze-repo-for-reuse.json` を n8n にインポートします。
2. `Set Config` ノードで対象リポジトリ（`repoOwner`, `repoName`）と LLM API キーを設定します。
3. 手動実行またはスケジュール実行を行います。
4. 出力された JSON ファイルをダウンロードし、RAG パイプラインやナレッジベースへ取り込みます。

## 注意事項

- LLM API キーは n8n Credentials または環境変数で管理し、ワークフローJSONにベタ書きしないでください。
- GitHub API のレート制限に留意してください。大規模リポジトリでは `recursive=1` の tree API 取得間隔を調整してください。
- `.md` 以外のファイルは自動的に除外されますが、機密を含む Markdown が `docs/` に存在しないか事前に確認してください。
