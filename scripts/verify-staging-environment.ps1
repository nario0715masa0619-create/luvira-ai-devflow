<#
.SYNOPSIS
  DevFlowステージング環境が本番と分離されていることを読み取り専用で確認します。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,
    [string]$Region = 'us-central1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($ProjectId -eq 'luvira-ai-control-plane') {
    throw '本番プロジェクトはステージング検証の対象にできません。'
}

$requiredApis = @('run.googleapis.com', 'cloudbuild.googleapis.com', 'artifactregistry.googleapis.com', 'firestore.googleapis.com', 'storage.googleapis.com')
$enabled = @(& gcloud services list --enabled --project $ProjectId --format='value(config.name)')
$missing = @($requiredApis | Where-Object { $_ -notin $enabled })
$projectNumber = (& gcloud projects describe $ProjectId --format='value(projectNumber)').Trim()
$bucket = "luvira-devflow-staging-$projectNumber"
$repository = & gcloud artifacts repositories describe luvira-devflow --location $Region --project $ProjectId --format='value(name)' 2>$null
$bucketExists = (& gcloud storage buckets describe "gs://$bucket" --project $ProjectId --format='value(name)' 2>$null) -eq "gs://$bucket"
$firestore = & gcloud firestore databases describe --database='(default)' --project $ProjectId --format='value(name)' 2>$null
$ready = $missing.Count -eq 0 -and $repository -and $bucketExists -and $firestore

[pscustomobject][ordered]@{
    status = if ($ready) { 'READY_FOR_DEPLOY_CONFIGURATION' } else { 'SETUP_INCOMPLETE' }
    project = $ProjectId
    isolated_from_production = $true
    missing_apis = $missing
    artifact_repository = [bool]$repository
    artifact_bucket = $bucketExists
    firestore = [bool]$firestore
    next_action = if ($ready) { 'GitHub Environment stagingのOIDCとSecretを設定してデプロイ設定へ進めます。' } else { 'prepare-staging-environment.ps1 を -Apply 付きで実行してください。' }
}
