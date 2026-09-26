param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('centralised_baseline', 'xmai_decision', 'xmai_explanation')]
    [string]$Condition,

    [Parameter(Mandatory = $true)]
    [int]$Seed,

    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $projectRoot 'run_carla_lead_braking.py'
$verificationPath = Join-Path $projectRoot 'verify_carla.py'
$startScript = Join-Path $projectRoot 'start_carla.ps1'
$stopScript = Join-Path $projectRoot 'stop_carla.ps1'
$runnerArguments = @('--condition', $Condition, '--seed', $Seed)
if ($OutputDirectory) {
    $runnerArguments += @('--output-dir', [System.IO.Path]::GetFullPath($OutputDirectory))
}

try {
    & $startScript -Offscreen
    if (-not $?) {
        throw 'CARLA start script did not complete successfully.'
    }

    $serverReady = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        & $pythonPath $verificationPath --timeout 5
        if ($LASTEXITCODE -eq 0) {
            $serverReady = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $serverReady) {
        throw 'CARLA did not become ready after 12 connection attempts.'
    }

    Start-Sleep -Seconds 2
    & $pythonPath $runnerPath @runnerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Experiment returned exit code $LASTEXITCODE."
    }
}
finally {
    & $stopScript
}
