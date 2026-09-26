$ErrorActionPreference = "Stop"
$prototypeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidPath = Join-Path $prototypeRoot ".carla.pid"

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Output "No CARLA PID file exists; nothing was stopped."
    exit 0
}

$carlaPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$carlaProcess = Get-Process -Id $carlaPid -ErrorAction SilentlyContinue
if ($null -eq $carlaProcess) {
    Remove-Item -LiteralPath $pidPath
    Write-Output "The recorded CARLA process is no longer running."
    exit 0
}

if ($carlaProcess.ProcessName -notlike "CarlaUE4*") {
    throw "PID $carlaPid belongs to $($carlaProcess.ProcessName), not CARLA; refusing to stop it."
}

Stop-Process -Id $carlaPid -Force -ErrorAction SilentlyContinue
Get-Process -Name "CarlaUE4*" -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
Write-Output "CARLA process $carlaPid and related simulator processes stopped."

