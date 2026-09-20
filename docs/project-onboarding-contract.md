# 新規プロダクト・オンボーディング契約

## 目的

`luvira-ai-devflow` は司令塔・承認・監査専用とし、プロダクトの変更先にはしない。
Orcaからの新規プロダクト依頼は、専用リポジトリが作成・初期化・接続確認されるまで
実装キューへ入れない。

## 状態遷移

`REQUESTED → APPROVED → PROVISIONING → READY`

- `REQUESTED`: リポジトリはまだ存在しない。依頼の下書き・承認対象だけを保持する。
- `APPROVED`: 人間が「この名称・所有者・非公開設定で新規作成する」と承認した状態。
- `PROVISIONING`: 専用の作成権限でリポジトリと初期構成を準備中。
- `READY`: リポジトリ名の一致、初期コミット、GitHub App接続の全てを確認済み。初めて実装受付を許可する。
- `PROVISION_FAILED`: 作成・初期化・接続確認のいずれかに失敗した終端状態。自動的に別リポジトリへ切り替えない。

`PROVISION_FAILED → APPROVED → PROVISIONING` の再試行は可能だが、同一の不変な依頼を
`human-approval` で改めて承認した場合だけに限る。失敗からの自動再試行や、別名の
リポジトリ作成はしない。すでに承認名と一致する非公開リポジトリが途中まで作成されて
いた場合だけ、そのリポジトリを再取得して初期設定を再開する。

## 権限分離

- 通常のDevFlow実装権限は、登録済みの一つのプロダクトリポジトリだけを対象にする。
- 新規リポジトリ作成は専用のプロビジョニング認証情報だけが担い、通常のWorker・OpenCode・PR公開認証情報と混在させない。
- GitHubの新規リポジトリ作成APIには `Administration: write`、初期ブランチ・初期設定の読書きには `Contents: read/write` が必要であるため、作成承認ごとにだけ利用する。通常の開発依頼には付与しない。
- GitHub Appの対象リポジトリ設定と初期コミットの両方を確認できなければ `READY` にしない。

## プロビジョニング実行の責務

専用プロビジョナーは、承認済みの `owner` と `slug` を用いて GitHub の
`/user/repos` API から**非公開**リポジトリを一件だけ作成する。応答の
`full_name` が承認内容と一致しなければ直ちに停止する。続いて
`.github/luvira-project.json` を初期コミットとして置く。このファイルには
プロジェクトID・司令塔リポジトリ・基点コミットだけを記録し、トークン・鍵・
Webhook secret は一切入れない。

## 実行承認と専用環境

GitHub Actionsの `Authorize project onboarding`（新規プロダクト作成の承認）は、通常の
`human-approval` Environmentで依頼の不変スナップショットを承認する。その後に、別の
`project-provisioning` Environmentで、リポジトリ作成だけを許可する。後者には
`PROJECT_PROVISIONING_TOKEN`（新規作成専用）だけを設定する。通常Workerの認証情報、
OpenCodeキー、既存プロダクトの公開認証情報を
このEnvironmentへ追加してはならない。

作成実行は、専用OIDC ID `github-project-provisioning` /
`devflow-project-provisioner` がControl Planeの`claim-provisioning`を呼べるように
設定されるまで失敗する。この設定が済む前にトークンだけでリポジトリを作成する
経路は存在しない。作成後はControl Planeが保持するGitHub Appで実インストールと
初期コミットを照合し、合わなければ`PROVISION_FAILED`へ終端する。Appの秘密鍵は
作成Environmentへ渡さない。

OIDCの固定条件と最小権限は
`security/workload-identity/github-project-provisioning.json` に記録する。契約と
workflowが食い違うPRは自動検証で停止する。

## 実装受付の分離

実装承認Issueは常に `luvira-ai-devflow` に作成する。これは承認履歴とWebhook入口を
一箇所に固定するためであり、実装先を同リポジトリに固定する意味ではない。Issue Formの
`Project ID` と `Repository` は、Control Planeが保持する台帳の `READY` レコードと一致
しなければ受理しない。受理後のWorker、成果物検証、PR公開はこの確定済みの専用プロダクト
リポジトリだけを対象にする。任意のリポジトリ名をフォームへ書いて権限を広げることはできない。

## 既存環境への影響

承認履歴・Webhook入口・運用通知は既存どおり `luvira-ai-devflow` に固定する。一方で、
実装先の選択は上記の台帳照合へ移す。台帳が未設定、対象プロダクトが `READY` でない、
またはフォームのリポジトリ名が一致しない場合は、既存の制御経路を含めて fail-closed で
停止する。
