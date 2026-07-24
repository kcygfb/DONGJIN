[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$client = Join-Path $projectDir "runtime\memurai\memurai-cli.exe"

if (-not (Test-Path -LiteralPath $client)) {
    throw "Memurai client not found: $client"
}

$service = Get-Service -Name "Memurai" -ErrorAction SilentlyContinue
if (-not $service) {
    Write-Host "Memurai: service not registered"
    exit 0
}

try {
    $ping = & $client -h 127.0.0.1 -p 6379 ping 2>$null
    $isRunning = $LASTEXITCODE -eq 0 -and $ping -eq "PONG"
} catch {
    $isRunning = $false
}

if (-not $isRunning) {
    Write-Host "Memurai: stopped (startup type: $($service.StartType))"
    exit 0
}

$memory = & $client -h 127.0.0.1 -p 6379 info memory
$used = $memory | Where-Object { $_ -match "^used_memory_human:" }
$limit = $memory | Where-Object { $_ -match "^maxmemory_human:" }
$policy = $memory | Where-Object { $_ -match "^maxmemory_policy:" }
$keys = & $client -h 127.0.0.1 -p 6379 dbsize

Write-Host "Memurai: running on 127.0.0.1:6379 (startup type: $($service.StartType))"
Write-Host $used
Write-Host $limit
Write-Host $policy
Write-Host "keys:$keys"
