[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$server = Join-Path $projectDir "runtime\memurai\memurai.exe"
$client = Join-Path $projectDir "runtime\memurai\memurai-cli.exe"

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

if (-not (Test-MemuraiConnection)) {
    Write-Host "Memurai is not running."
    exit 0
}

$isAdministrator = (
    New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdministrator) {
    Stop-Service -Name "Memurai"
    $stopExitCode = 0
} else {
    $command = "Stop-Service -Name 'Memurai'"
    $encoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($command)
    )
    $powershell = Join-Path $env:SystemRoot `
        "System32\WindowsPowerShell\v1.0\powershell.exe"

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
    $stopExitCode = $elevated.ExitCode
}

$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    Start-Sleep -Milliseconds 200
    $isRunning = Test-MemuraiConnection
} while ($isRunning -and [DateTime]::UtcNow -lt $deadline)

if ($isRunning) {
    throw "Memurai failed to stop within 10 seconds (exit code $stopExitCode)."
}

Write-Host "Memurai stopped."
