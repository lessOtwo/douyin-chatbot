param([switch]$Production, [switch]$Mock, [switch]$ConfigOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Mock) { throw 'Legacy Typebot mock is retired. Use the full local stack; SEND_MODE=mock prevents real channel sends.' }
$envPath = Join-Path $PSScriptRoot '.env'
$content = if (Test-Path -LiteralPath $envPath) { [IO.File]::ReadAllText($envPath) } else { '' }
foreach ($line in [IO.File]::ReadAllLines((Join-Path $PSScriptRoot '.env.example'))) {
    if ($line -match '^([A-Z0-9_]+)=(.*)$') {
        $key = $Matches[1]; $value = $Matches[2]
        if ($content -match "(?m)^${key}=") { continue }
        if ($value -eq 'GENERATE') {
            $length = if ($key -eq 'ENCRYPTION_SECRET') { 16 } else { 24 }
            $value = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes($length)).ToLowerInvariant()
        }
        $content = $content.TrimEnd() + "`n$key=$value`n"
    }
}
[IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))
$accountsPath = Join-Path $PSScriptRoot 'config/accounts.json'
if (-not (Test-Path -LiteralPath $accountsPath)) {
    [IO.File]::WriteAllText($accountsPath, "[]`n", [Text.UTF8Encoding]::new($false))
}
$composeArgs = @('compose', '-f', 'compose.yaml')
if ($Production) {
    $composeArgs += @('-f', 'compose.production.yaml')
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $PSScriptRoot '.env'))) {
        if ($line -match '^([A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
    }
    foreach ($key in @('BUSINESS_HOST','FASTGPT_HOST')) {
        if ([string]::IsNullOrWhiteSpace($values[$key])) { throw "Set $key in .env before production startup." }
    }
    foreach ($key in @('BUSINESS_URL','FASTGPT_PUBLIC_URL')) {
        if ($values[$key] -notmatch '^https://[^/]+') { throw "Set $key to its HTTPS public URL." }
    }
}
& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose validation failed.' }
if ($ConfigOnly) { Write-Host 'Compose configuration is valid.'; exit 0 }
& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop / Docker Engine first.' }
$startArgs = @('up', '-d', '--build', '--wait', '--wait-timeout', '600')
& docker @composeArgs @startArgs
if ($LASTEXITCODE -ne 0) { throw 'Startup incomplete. Inspect docker compose logs; persisted data has been retained.' }
& docker @composeArgs ps
Write-Host 'Services started. Business admin: http://localhost:8088.'
Write-Host 'FastGPT: http://localhost:3000.'
Write-Host 'See docs/PLAN.md for implementation status. Real sending stays disabled by default.'

