<#
.SYNOPSIS
  ステージングGitHub Webhook署名シークレットを改行なしで登録します。

.DESCRIPTION
  Webhook署名は1バイトでも異なると受理されません。秘密値を標準入力や
  コマンド引数で渡さず、UTF-8・改行なしのファイルだけを受け入れます。
  本番プロジェクトは明示的に拒否します。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$SecretPath,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($ProjectId -eq 'luvira-ai-control-plane') {
    throw '本番プロジェクトへステージングWebhook署名シークレットを登録できません。'
}

$secretBytes = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $SecretPath))
if ($secretBytes.Length -eq 0) {
    throw 'Webhook署名シークレットは空にできません。'
}
if ($secretBytes -contains 10 -or $secretBytes -contains 13) {
    throw 'Webhook署名シークレットに改行を含めることはできません。改行なしのUTF-8ファイルを指定してください。'
}

$arguments = @('secrets', 'versions', 'add', 'github-webhook-signing-secret-staging', '--project', $ProjectId, "--data-file=$SecretPath")
if (-not $Apply) {
    Write-Host '[計画] 改行なしのステージングWebhook署名シークレットを新しいバージョンとして登録します。'
    exit 0
}

& gcloud @arguments
if ($LASTEXITCODE -ne 0) { throw 'ステージングWebhook署名シークレットの登録に失敗しました。' }
Write-Host 'ステージングWebhook署名シークレットを改行なしで登録しました。入口サービスを再デプロイして最新バージョンを読み込ませてください。'
