[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,38}$')] [string] $Owner,
    [Parameter(Mandatory)] [ValidatePattern('^[a-z][a-z0-9-]{2,62}$')] [string] $Slug,
    [Parameter(Mandatory)] [ValidateLength(1, 160)] [string] $Description,
    [string] $N8nWebhookUrl = $env:DEVFLOW_ORCA_PROJECT_N8N_WEBHOOK_URL,
    [switch] $Submit
)

$request = [ordered]@{
    owner = $Owner
    slug = $Slug
    description = $Description.Trim()
    visibility = 'private'
}

if (-not $Submit) {
    $request | ConvertTo-Json
    Write-Host '下書きです。送信する場合は -Submit を付けて実行してください。'
    exit 0
}

if ([string]::IsNullOrWhiteSpace($N8nWebhookUrl)) {
    throw 'DEVFLOW_ORCA_PROJECT_N8N_WEBHOOK_URL が未設定です。新規プロダクト用のn8n受付URLを設定してください。'
}
if ([string]::IsNullOrWhiteSpace($env:DEVFLOW_ORCA_N8N_INTAKE_KEY)) {
    throw 'DEVFLOW_ORCA_N8N_INTAKE_KEY が未設定です。受付キーを設定してください。'
}

try {
    $response = Invoke-RestMethod -Method Post -Uri $N8nWebhookUrl `
        -Headers @{ 'X-DevFlow-Intake-Key' = $env:DEVFLOW_ORCA_N8N_INTAKE_KEY } `
        -ContentType 'application/json' -Body ($request | ConvertTo-Json -Compress) -ErrorAction Stop
} catch {
    throw "新規プロダクト受付に失敗しました。n8nの『Orca Project Onboarding Intake v1』が公開済みか、URLと認証情報が一致しているか確認してください。詳細: $($_.Exception.Message)"
}

if ($response.status -ne 'AWAITING_HUMAN_APPROVAL' -or [string]::IsNullOrWhiteSpace($response.project_id) -or [string]::IsNullOrWhiteSpace($response.issue_url)) {
    throw '新規プロダクト受付が不完全な応答を返しました。Issue作成は確認できていません。'
}

$response | ConvertTo-Json
