param(
    # Retained for existing commands; off-screen collection is now always enabled.
    [switch]$OffScreen
)

$ErrorActionPreference = "Stop"
$prototypeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configuration = Get-Content -LiteralPath (Join-Path $prototypeRoot "config\carla.json") -Raw | ConvertFrom-Json
$serverPath = Join-Path $configuration.install_root "CarlaUE4.exe"
$cachePath = Join-Path $prototypeRoot $configuration.cache_directory
$pidPath = Join-Path $prototypeRoot ".carla.pid"

if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) {
    throw "CARLA server was not found at $serverPath"
}

New-Item -ItemType Directory -Force -Path $cachePath | Out-Null
$env:CARLA_CACHE_DIR = $cachePath

$serverArguments = @(
    "-quality-level=$($configuration.quality_level)",
    "-carla-rpc-port=$($configuration.rpc_port)",
    "-RenderOffScreen",
    "-nosound"
)

$launchOptions = @{
    FilePath = $serverPath
    WorkingDirectory = $configuration.install_root
    ArgumentList = $serverArguments
    PassThru = $true
    WindowStyle = "Hidden"
}

$carlaProcess = Start-Process @launchOptions
Set-Content -LiteralPath $pidPath -Value $carlaProcess.Id -Encoding ascii
Write-Output "CARLA started for data collection without a display or sound (process $($carlaProcess.Id), port $($configuration.rpc_port))."
