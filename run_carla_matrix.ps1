param(
    [int[]]$Seeds,
    [string[]]$Conditions = @(
        'centralised_baseline',
        'xmai_decision',
        'xmai_explanation'
    )
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $projectRoot 'config\carla.json'
$outputDirectory = Join-Path $projectRoot 'output\carla_lead_braking'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runnerPath = Join-Path $projectRoot 'run_carla_lead_braking.py'
$analysisPath = Join-Path $projectRoot 'analyze_carla_lead_braking.py'
$verificationPath = Join-Path $projectRoot 'verify_carla.py'
$startScript = Join-Path $projectRoot 'start_carla.ps1'
$stopScript = Join-Path $projectRoot 'stop_carla.ps1'

if ($null -eq $Seeds -or $Seeds.Count -eq 0) {
    $configuration = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $Seeds = @($configuration.lead_braking.seeds | ForEach-Object { [int]$_ })
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$startedAt = Get-Date
$runs = @()
$totalRuns = $Seeds.Count * $Conditions.Count
$runNumber = 0

foreach ($seed in $Seeds) {
    foreach ($condition in $Conditions) {
        $runNumber++
        Write-Host "[$runNumber/$totalRuns] Starting condition=$condition seed=$seed"
        $status = 'failed'
        $message = ''
        $runStartedAt = Get-Date
        try {
            & $startScript -Offscreen
            if (-not $?) {
                throw "CARLA start script did not complete successfully."
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
                throw "CARLA did not become ready after 12 connection attempts."
            }
            Start-Sleep -Seconds 2
            & $pythonPath $runnerPath --condition $condition --seed $seed
            if ($LASTEXITCODE -ne 0) {
                throw "Experiment returned exit code $LASTEXITCODE."
            }
            $status = 'complete'
        }
        catch {
            $message = $_.Exception.Message
            Write-Warning "Run failed: $message"
        }
        finally {
            try {
                & $stopScript
            }
            catch {
                Write-Warning "CARLA cleanup warning: $($_.Exception.Message)"
            }
        }
        $runs += [ordered]@{
            seed = $seed
            condition = $condition
            status = $status
            message = $message
            started_at = $runStartedAt.ToString('o')
            duration_seconds = [math]::Round(((Get-Date) - $runStartedAt).TotalSeconds, 3)
        }
    }
}

& $pythonPath $analysisPath
$analysisExitCode = $LASTEXITCODE
$manifest = [ordered]@{
    experiment = 'CARLA lead-vehicle braking repeated evaluation'
    started_at = $startedAt.ToString('o')
    completed_at = (Get-Date).ToString('o')
    requested_runs = $totalRuns
    completed_runs = @($runs | Where-Object { $_.status -eq 'complete' }).Count
    failed_runs = @($runs | Where-Object { $_.status -eq 'failed' }).Count
    analysis_exit_code = $analysisExitCode
    runs = $runs
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $outputDirectory 'matrix_manifest.json') -Encoding UTF8

Write-Host "Matrix complete: $($manifest.completed_runs)/$totalRuns runs succeeded."
if ($manifest.failed_runs -gt 0 -or $analysisExitCode -ne 0) {
    exit 2
}
