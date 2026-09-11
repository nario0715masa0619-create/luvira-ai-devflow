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
$TaskCollection = "https://firestore.googleapis.com/v1/projects/$ProjectId/databases/(default)/documents/devflow_execution_tasks?pageSize=100"

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

function Get-TaskView {
    param(
        [object[]]$Documents,
        [datetime]$Now
    )

    # 未終端の古い移行記録を、現在の承認待ち・実行中として誤表示しない。
    # ID・状態・時刻だけを返し、TaskSpecやプロンプトなどの内容は表示しない。
    $live = @()
    $historical = @()
    foreach ($document in $Documents) {
        $fields = $document.fields
        $statusProperty = $fields.PSObject.Properties['status']
        if ($null -eq $statusProperty) { continue }
        $status = $statusProperty.Value.stringValue
        if ($status -in @('MERGED', 'EXECUTION_FAILED_FINAL', 'CANCELLED', 'EXPIRED', 'REJECTED')) { continue }

        $updatedAt = [datetime]::Parse($document.updateTime).ToUniversalTime()
        $record = [pscustomobject][ordered]@{
            task_id = ($document.name -split '/')[-1]
            status = $status
            updated_at = $updatedAt.ToString('o')
        }
        if (($Now - $updatedAt).TotalHours -gt 24) {
            $historical += $record
        } else {
            $live += $record
        }
    }

    return [pscustomobject][ordered]@{
        active = @($live | Where-Object { $_.status -ne 'AWAITING_HUMAN_APPROVAL' })
        awaiting_human_approval = @($live | Where-Object { $_.status -eq 'AWAITING_HUMAN_APPROVAL' })
        historical_unresolved = @($historical)
    }
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
    $taskDocuments = (Invoke-RestMethod -Method Get -Headers $headers -Uri $TaskCollection).documents
    $now = (Get-Date).ToUniversalTime()
    $tasks = Get-TaskView -Documents $taskDocuments -Now $now

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
        tasks = $tasks
        next_action = if (-not $isReady) {
            '自動実行を増やさず、BLOCKEDの構成要素と運用Issueを確認してください。'
        } elseif ($tasks.awaiting_human_approval.Count -gt 0) {
            '新しい承認待ちタスクがあります。GitHubの保護済み承認フローで判断してください。'
        } elseif ($tasks.active.Count -gt 0) {
            '実行中タスクを監督してください。Cloud RunやWorkerを手動で停止・再起動する必要はありません。'
        } else {
            '監督のみ。承認済みの新規タスクがあればDevFlowが処理します。'
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
