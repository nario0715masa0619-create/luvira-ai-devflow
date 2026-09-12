<#
.SYNOPSIS
  GitHub ActionsからステージングだけへデプロイするOIDC信頼を設定します。

.DESCRIPTION
  本番プロジェクトを拒否します。既定では作成計画だけを表示し、-Apply 時だけ
  Workload Identity Federationと最小限のステージング用ロールを作成します。
  信頼条件は、このリポジトリ・mainブランチ・ステージングデプロイワークフローに
  限定します。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($ProjectId -eq 'luvira-ai-control-plane') {
    throw '本番プロジェクトにはステージング用のGitHub信頼を設定できません。'
}

function Invoke-Gcloud {
    param([string[]]$Arguments)
    # IAM policy bindings are optimistic read-modify-write updates.  Another
    # binding in this same setup can legitimately update the ETag first, so
    # retry only that transient conflict with bounded backoff.
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $result = & gcloud @Arguments 2>&1
        if ($LASTEXITCODE -eq 0) { return ($result -join "`n") }
        $message = $result -join "`n"
        if ($message -notmatch 'subject of a conflict|ETag.*did not match' -or $attempt -eq 5) {
            throw $message
        }
        Start-Sleep -Seconds ([math]::Pow(2, $attempt - 1))
    }
}

function Test-GcloudResource {
    param([string[]]$Arguments)
    & gcloud @Arguments *> $null
    return $LASTEXITCODE -eq 0
}

function Invoke-PlanOrApply {
    param([string[]]$Arguments)
    if (-not $Apply) {
        Write-Host ('[計画] gcloud ' + ($Arguments -join ' '))
        return
    }
    Invoke-Gcloud $Arguments | Out-Null
    Write-Host ('[設定済み] ' + ($Arguments -join ' '))
}

$projectNumber = Invoke-Gcloud @('projects', 'describe', $ProjectId, '--format=value(projectNumber)')
$repository = 'nario0715masa0619-create/luvira-ai-devflow'
$pool = 'github-actions'
$provider = 'github-deployer'
$deployerAccount = "devflow-deployer-staging@$ProjectId.iam.gserviceaccount.com"
$orchestratorAccount = "devflow-orchestrator-staging@$ProjectId.iam.gserviceaccount.com"
$principal = "principalSet://iam.googleapis.com/projects/$projectNumber/locations/global/workloadIdentityPools/$pool/attribute.repository/$repository"
$condition = "assertion.repository == '$repository' && assertion.ref == 'refs/heads/main' && assertion.workflow == 'Deploy DevFlow staging'"
$sourceBucket = "luvira-devflow-staging-source-$projectNumber"

if (-not (Test-GcloudResource @('iam', 'workload-identity-pools', 'describe', $pool, '--project', $ProjectId, '--location', 'global'))) {
    Invoke-PlanOrApply @('iam', 'workload-identity-pools', 'create', $pool, '--project', $ProjectId, '--location', 'global', '--display-name=GitHub Actions')
}
if (-not (Test-GcloudResource @('iam', 'workload-identity-pools', 'providers', 'describe', $provider, '--workload-identity-pool', $pool, '--project', $ProjectId, '--location', 'global'))) {
    Invoke-PlanOrApply @('iam', 'workload-identity-pools', 'providers', 'create-oidc', $provider, '--workload-identity-pool', $pool, '--project', $ProjectId, '--location', 'global', '--issuer-uri=https://token.actions.githubusercontent.com', '--allowed-audiences=https://github.com/nario0715masa0619-create', '--attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository', "--attribute-condition=$condition")
}

Invoke-PlanOrApply @('iam', 'service-accounts', 'add-iam-policy-binding', $deployerAccount, '--project', $ProjectId, '--role=roles/iam.workloadIdentityUser', "--member=$principal")
Invoke-PlanOrApply @('iam', 'service-accounts', 'add-iam-policy-binding', $deployerAccount, '--project', $ProjectId, '--role=roles/iam.serviceAccountOpenIdTokenCreator', "--member=$principal")
foreach ($role in @('roles/artifactregistry.admin', 'roles/cloudbuild.editor', 'roles/iam.serviceAccountUser', 'roles/run.admin', 'roles/serviceusage.serviceUsageConsumer', 'roles/storage.bucketViewer')) {
    Invoke-PlanOrApply @('projects', 'add-iam-policy-binding', $ProjectId, "--member=serviceAccount:$deployerAccount", "--role=$role")
}
foreach ($role in @('roles/datastore.user', 'roles/logging.viewer', 'roles/run.developer', 'roles/storage.objectViewer')) {
    Invoke-PlanOrApply @('projects', 'add-iam-policy-binding', $ProjectId, "--member=serviceAccount:$orchestratorAccount", "--role=$role")
}
Invoke-PlanOrApply @('storage', 'buckets', 'add-iam-policy-binding', "gs://$sourceBucket", "--member=serviceAccount:$deployerAccount", '--role=roles/storage.objectAdmin')

[pscustomobject][ordered]@{
    staging_wif_provider = "projects/$projectNumber/locations/global/workloadIdentityPools/$pool/providers/$provider"
    staging_deployer_service_account = $deployerAccount
    staging_orchestrator_service_account = $orchestratorAccount
    staging_worker_service_account = "devflow-worker-staging@$ProjectId.iam.gserviceaccount.com"
} | Format-List

if (-not $Apply) { Write-Host '計画のみ完了しました。実設定は -Apply を付けて実行してください。' }
