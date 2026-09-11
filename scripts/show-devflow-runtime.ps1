<#
.SYNOPSIS
  Orca用の読み取り専用DevFlow運用サマリーを表示します。

.DESCRIPTION
  Cloud SchedulerとFirestoreの公開済み運用記録だけを読み取ります。
  タスク、承認、Worker、GitHub、Secret Manager、IAMには変更を加えません。
  OrcaのターミナルまたはAutomationから実行するための入口です。
#>
[CmdletBinding()]
param(
    [switch]$AsJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectId = 'luvira-ai-control-plane'
$Region = 'us-central1'
$SchedulerJob = 'luvira-devflow-autonomous-broker'
$RuntimeHealthDocument = "https://firestore.googleapis.com/v1/projects/$ProjectId/databases/(default)/documents/devflow_runtime_health/current"

function Get-CommandJson {
    param([string[]]$Arguments)

    $output = & gcloud @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "DevFlowの状態を取得できませんでした: $($output -join ' ')"
    }
    return ($output -join "`n") | ConvertFrom-Json
}

function ConvertFrom-FirestoreValue {
    param($Value)

    if ($null -eq $Value) { return $null }
    $properties = $Value.PSObject.Properties
    if ($null -ne $properties['stringValue']) { return $properties['stringValue'].Value }
    if ($null -ne $properties['timestampValue']) { return $properties['timestampValue'].Value }
    if ($null -ne $properties['integerValue']) { return [int64]$properties['integerValue'].Value }
    if ($null -ne $properties['doubleValue']) { return [double]$properties['doubleValue'].Value }
    if ($null -ne $properties['booleanValue']) { return [bool]$properties['booleanValue'].Value }
    if ($null -ne $properties['nullValue']) { return $null }
    if ($null -ne $properties['mapValue']) {
        $result = [ordered]@{}
        foreach ($entry in $properties['mapValue'].Value.fields.PSObject.Properties) {
            $result[$entry.Name] = ConvertFrom-FirestoreValue $entry.Value
        }
        return [pscustomobject]$result
    }
    if ($null -ne $properties['arrayValue']) {
        return @($properties['arrayValue'].Value.values | ForEach-Object { ConvertFrom-FirestoreValue $_ })
    }
    throw '想定外のFirestore値形式です。'
}

try {
    $scheduler = Get-CommandJson @(
        'scheduler', 'jobs', 'describe', $SchedulerJob,
        '--project', $ProjectId,
        '--location', $Region,
        '--format=json(state,schedule,lastAttemptTime)'
    )

    $accessToken = (& gcloud auth print-access-token 2>&1)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($accessToken -join '').Trim())) {
        throw 'Google Cloudの読み取り認証を取得できませんでした。'
    }
    $headers = @{ Authorization = "Bearer $(($accessToken -join '').Trim())" }
    $document = Invoke-RestMethod -Method Get -Headers $headers -Uri $RuntimeHealthDocument
    $health = ConvertFrom-FirestoreValue ([pscustomobject]@{ mapValue = @{ fields = $document.fields } })

    $isReady = $scheduler.state -eq 'ENABLED' -and $health.status -eq 'READY'
    $summary = [pscustomobject][ordered]@{
        checked_at = (Get-Date).ToUniversalTime().ToString('o')
        overall = if ($isReady) { 'READY' } else { 'ATTENTION_REQUIRED' }
        scheduler = [pscustomobject][ordered]@{
            state = $scheduler.state
            schedule = $scheduler.schedule
            last_attempt_at = $scheduler.lastAttemptTime
        }
        runtime = [pscustomobject][ordered]@{
            status = $health.status
            checked_at = $health.checked_at
            alert = $health.alert
            checks = $health.checks
        }
        next_action = if ($isReady) {
            '監督のみ。承認済みの新規タスクがあればDevFlowが処理します。'
        } else {
            '自動実行を増やさず、BLOCKEDの構成要素と運用Issueを確認してください。'
        }
    }

    if ($AsJson) {
        $summary | ConvertTo-Json -Depth 6
    } else {
        $summary
    }
} catch {
    Write-Error $_
    exit 1
}
