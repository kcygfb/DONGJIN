[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeDir = Join-Path $projectDir "runtime\memurai"
$server = Join-Path $runtimeDir "memurai.exe"
$client = Join-Path $runtimeDir "memurai-cli.exe"

if (-not (Test-Path -LiteralPath $server)) {
    throw "Memurai runtime not found: $server"
}
if (-not (Test-Path -LiteralPath $client)) {
    throw "Memurai client not found: $client"
}

function Test-MemuraiConnection {
    try {
        $output = & $client -h 127.0.0.1 -p 6379 ping 2>$null
        return $LASTEXITCODE -eq 0 -and $output -eq "PONG"
    } catch {
        return $false
    }
}

if (Test-MemuraiConnection) {
    Write-Host "Memurai is already running on 127.0.0.1:6379."
    exit 0
}

$service = Get-Service -Name "Memurai" -ErrorAction SilentlyContinue
if (-not $service) {
    throw "Memurai service is not registered. See 文档\操作指南\启动指南.md for setup details."
}

$isAdministrator = (
    New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdministrator) {
    Start-Service -Name "Memurai"
    $startExitCode = 0
} else {
    $command = "Start-Service -Name 'Memurai'"
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($command)
    )
    $powershell = Join-Path $env:SystemRoot `
        "System32\WindowsPowerShell\v1.0\powershell.exe"

    # This machine can expose both Path and PATH. Normalize them before
    # Start-Process so PowerShell does not reject the inherited environment.
    $pathValue = $env:Path
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")

    $elevated = Start-Process `
        -FilePath $powershell `
        -Verb RunAs `
        -ArgumentList "-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    $startExitCode = $elevated.ExitCode
}

$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    Start-Sleep -Milliseconds 200
    $isReady = Test-MemuraiConnection
} while (-not $isReady -and [DateTime]::UtcNow -lt $deadline)

if (-not $isReady) {
    throw "Memurai failed to start (exit code $startExitCode). Check $runtimeDir\memurai-log.txt."
}

Write-Host "Memurai started manually on 127.0.0.1:6379."
