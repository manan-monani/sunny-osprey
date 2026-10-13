# Sunny Osprey - one-click start (Windows). Run via start.bat in the project root.
# Starts Docker Desktop if needed, builds the model on first run, starts every service
# (model server, webcam pipeline, dashboard, private alerts, camera relay) and opens the pages.
# -NoBrowser: start everything without opening browser tabs (e.g. for running at login).
param([switch]$NoBrowser)

# Continue: docker writes normal progress to stderr, which "Stop" would turn into a fatal error;
# every docker call is checked through $LASTEXITCODE instead
$ErrorActionPreference = "Continue"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir
$Compose = @("compose", "-f", "docker-compose.webcam.yaml")

# Camera publish page: H.264 (the pipeline cannot decode the browser's default AV1), 720p@15fps, no microphone
$PublishUrl = "http://localhost:8889/webcam/publish?video-codec=h264%2F90000&video-width=1280&video-height=720&video-framerate=15&video-bitrate=2500&audio-device=none"
$DashboardUrl = "http://localhost:8080"
$Topic = (Select-String -Path "sunny-osprey-config.yaml" -Pattern '^\s+topic:\s*(\S+)' | Select-Object -First 1).Matches.Groups[1].Value
$AlertsUrl = "http://localhost:8090/$Topic"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

function Wait-Url($url, $name, $timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            if ((Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) {
                Write-Host "    $name is ready" -ForegroundColor Green
                return
            }
        } catch { }
        Start-Sleep -Seconds 3
    }
    Fail "$name did not become ready within $timeoutSec s. Check: docker compose -f docker-compose.webcam.yaml logs"
}

Write-Host "Sunny Osprey - AI security monitor (Gemma 3n on llama.cpp)" -ForegroundColor Yellow

# 1. Docker Desktop
Step "Checking Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker is not installed (https://www.docker.com/products/docker-desktop/)." }
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Starting Docker Desktop..."
    Start-Process "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        docker info *> $null
    } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop did not start within 3 minutes." }
}
Write-Host "    Docker is running" -ForegroundColor Green

# 2. Model files (first run only: ~11 GB download + ~10 min conversion)
Step "Checking model files"
if (-not (Test-Path "models\gguf\gemma-3n-E2B-it-Q4_K_M.gguf") -or -not (Test-Path "models\gguf\mmproj-gemma-3n-E2B-it-f16.gguf")) {
    Write-Host "    Not found - building Gemma 3n now (first run only, takes a while)..."
    docker @Compose --profile setup run --rm model-prep
    if ($LASTEXITCODE -ne 0) { Fail "Model build failed (see output above)." }
}
Write-Host "    Gemma 3n model present" -ForegroundColor Green

# 3. All services
Step "Starting services (model server, webcam pipeline, dashboard, alerts, camera relay)"
docker @Compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (see output above)." }

Step "Waiting for services"
Wait-Url "http://localhost:8081/health" "Model server (Gemma 3n)" 600
Wait-Url "$DashboardUrl/api/health" "Dashboard" 120
Wait-Url "http://localhost:8090/v1/health" "Private alerts (ntfy)" 60

# 4. Pages
if (-not $NoBrowser) {
    Step "Opening pages in your browser"
    Start-Process $AlertsUrl
    Start-Process $DashboardUrl
    Start-Process $PublishUrl
}

Write-Host @"

Sunny Osprey is running.
  1. Camera tab: allow the camera, click "Publish" and keep that tab open
  2. Alerts tab: click "Grant now" once to get desktop notifications
     $AlertsUrl
  3. Dashboard:  $DashboardUrl

Live log:  docker compose -f docker-compose.webcam.yaml logs -f webcam
Stop all:  stop.bat
"@ -ForegroundColor Green
