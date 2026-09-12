<#
.SYNOPSIS
  分離されたDevFlowステージング環境の基盤を検証・準備します。

.DESCRIPTION
  本番プロジェクトを明示的に拒否します。既定では変更せず不足項目だけを
  表示します。-Apply を指定した場合だけ、ステージング専用プロジェクトに
  必要なAPI、Artifact Registry、Cloud Storageバケット、サービスアカウントを
  作成します。デプロイやGitHubへの書込み、実行タスクの起動は行いません。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,
    [string]$Region = 'us-central1',
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProductionProject = 'luvira-ai-control-plane'
if ($ProjectId -eq $ProductionProject) {
    throw '本番プロジェクトはステージング準備の対象にできません。専用のGCPプロジェクトIDを指定してください。'
}

function Invoke-GcloudRead {
    param([string[]]$Arguments)
    $result = & gcloud @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($result -join "`n") }
    return ($result -join "`n")
}

function Invoke-GcloudApply {
    param([string[]]$Arguments)
    if (-not $Apply) {
        Write-Host ('[計画] gcloud ' + ($Arguments -join ' '))
        return
    }
    Invoke-GcloudRead $Arguments | Out-Null
    Write-Host ('[作成済み] ' + ($Arguments -join ' '))
}

function Test-GcloudResource {
    param([string[]]$Arguments)
    & gcloud @Arguments *> $null
    return $LASTEXITCODE -eq 0
}

$projectNumber = Invoke-GcloudRead @('projects', 'describe', $ProjectId, '--format=value(projectNumber)')
if ([string]::IsNullOrWhiteSpace($projectNumber)) { throw 'ステージングプロジェクトを確認できませんでした。' }

$artifactRepository = 'luvira-devflow'
$artifactBucket = "luvira-devflow-staging-$projectNumber"
$sourceBucket = "luvira-devflow-staging-source-$projectNumber"
$orchestratorServiceAccountId = 'devflow-orchestrator-staging'
$workerServiceAccountId = 'devflow-worker-staging'
$deployerServiceAccountId = 'devflow-deployer-staging'
$webhookServiceAccountId = 'devflow-github-webhook-staging'
foreach ($serviceAccountId in @($orchestratorServiceAccountId, $workerServiceAccountId, $deployerServiceAccountId, $webhookServiceAccountId)) {
    if ($serviceAccountId.Length -lt 6 -or $serviceAccountId.Length -gt 30) {
        throw "サービスアカウントIDの長さがGCP制限外です: $serviceAccountId"
    }
}
$orchestratorServiceAccount = "$orchestratorServiceAccountId@$ProjectId.iam.gserviceaccount.com"
$workerServiceAccount = "$workerServiceAccountId@$ProjectId.iam.gserviceaccount.com"
$deployerServiceAccount = "$deployerServiceAccountId@$ProjectId.iam.gserviceaccount.com"
$webhookServiceAccount = "$webhookServiceAccountId@$ProjectId.iam.gserviceaccount.com"

Write-Host "対象: $ProjectId ($Region)"
Write-Host "成果物バケット: gs://$artifactBucket"
Write-Host "ビルドソースバケット: gs://$sourceBucket"
Write-Host 'この操作は本番・GitHub・既存タスクには変更を加えません。'

Invoke-GcloudApply @('services', 'enable', 'run.googleapis.com', 'cloudbuild.googleapis.com', 'artifactregistry.googleapis.com', 'firestore.googleapis.com', 'storage.googleapis.com', 'secretmanager.googleapis.com', '--project', $ProjectId)
if (-not (Test-GcloudResource @('artifacts', 'repositories', 'describe', $artifactRepository, '--location', $Region, '--project', $ProjectId))) {
    Invoke-GcloudApply @('artifacts', 'repositories', 'create', $artifactRepository, '--repository-format=docker', '--location', $Region, '--project', $ProjectId, '--description=DevFlow staging images')
}
if (-not (Test-GcloudResource @('storage', 'buckets', 'describe', "gs://$artifactBucket", '--project', $ProjectId))) {
    Invoke-GcloudApply @('storage', 'buckets', 'create', "gs://$artifactBucket", '--location', $Region, '--project', $ProjectId, '--uniform-bucket-level-access')
}
if (-not (Test-GcloudResource @('storage', 'buckets', 'describe', "gs://$sourceBucket", '--project', $ProjectId))) {
    Invoke-GcloudApply @('storage', 'buckets', 'create', "gs://$sourceBucket", '--location', $Region, '--project', $ProjectId, '--uniform-bucket-level-access')
}
if (-not (Test-GcloudResource @('firestore', 'databases', 'describe', '--database=(default)', '--project', $ProjectId))) {
    Invoke-GcloudApply @('firestore', 'databases', 'create', '--database=(default)', '--location', $Region, '--type=firestore-native', '--project', $ProjectId)
}
if (-not (Test-GcloudResource @('secrets', 'describe', 'opencode-go-api-key-staging', '--project', $ProjectId))) {
    Invoke-GcloudApply @('secrets', 'create', 'opencode-go-api-key-staging', '--project', $ProjectId, '--replication-policy=automatic', '--labels=environment=staging,component=devflow')
}
if (-not (Test-GcloudResource @('secrets', 'describe', 'github-webhook-signing-secret-staging', '--project', $ProjectId))) {
    Invoke-GcloudApply @('secrets', 'create', 'github-webhook-signing-secret-staging', '--project', $ProjectId, '--replication-policy=automatic', '--labels=environment=staging,component=devflow')
}
if (-not (Test-GcloudResource @('iam', 'service-accounts', 'describe', $orchestratorServiceAccount, '--project', $ProjectId))) {
    Invoke-GcloudApply @('iam', 'service-accounts', 'create', $orchestratorServiceAccountId, '--project', $ProjectId, '--display-name=DevFlow staging orchestrator')
}
if (-not (Test-GcloudResource @('iam', 'service-accounts', 'describe', $workerServiceAccount, '--project', $ProjectId))) {
    Invoke-GcloudApply @('iam', 'service-accounts', 'create', $workerServiceAccountId, '--project', $ProjectId, '--display-name=DevFlow staging isolated worker')
}
if (-not (Test-GcloudResource @('iam', 'service-accounts', 'describe', $deployerServiceAccount, '--project', $ProjectId))) {
    Invoke-GcloudApply @('iam', 'service-accounts', 'create', $deployerServiceAccountId, '--project', $ProjectId, '--display-name=DevFlow staging deployer')
}
if (-not (Test-GcloudResource @('iam', 'service-accounts', 'describe', $webhookServiceAccount, '--project', $ProjectId))) {
    Invoke-GcloudApply @('iam', 'service-accounts', 'create', $webhookServiceAccountId, '--project', $ProjectId, '--display-name=DevFlow staging GitHub webhook ingress')
}

Write-Host ''
Write-Host '次の入力値（GitHub Environment: staging）:'
[pscustomobject][ordered]@{
    GCP_PROJECT_ID = $ProjectId
    GCP_REGION = $Region
    GCP_ARTIFACT_REPOSITORY = $artifactRepository
    STAGING_ARTIFACT_BUCKET = $artifactBucket
    STAGING_SOURCE_BUCKET = $sourceBucket
    STAGING_ORCHESTRATOR_SERVICE_ACCOUNT = $orchestratorServiceAccount
    STAGING_WORKER_SERVICE_ACCOUNT = $workerServiceAccount
    STAGING_DEPLOYER_SERVICE_ACCOUNT = $deployerServiceAccount
    STAGING_WEBHOOK_SERVICE_ACCOUNT = $webhookServiceAccount
    V3_TASK_COLLECTION = 'devflow_staging_execution_tasks'
    V3_VERIFIED_ARTIFACT_COLLECTION = 'devflow_staging_verified_artifacts'
    V3_IMPLEMENTATION_ARTIFACT_COLLECTION = 'devflow_staging_verified_implementation_artifacts'
    V3_RUNTIME_HEALTH_COLLECTION = 'devflow_staging_runtime_health'
    STAGING_OPENCODE_ENABLED = 'false'
    STAGING_OPENCODE_SECRET = 'opencode-go-api-key-staging (値はここへ保存し、GitHubへは保存しない)'
    STAGING_GITHUB_INTEGRATION_ENABLED = 'false'
    STAGING_GITHUB_WEBHOOK_SECRET = 'github-webhook-signing-secret-staging (値はここへ保存し、GitHubへは保存しない)'
} | Format-List

if (-not $Apply) {
    Write-Host '計画のみ完了しました。実作成は -Apply を付けて実行してください。'
}
