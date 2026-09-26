param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$serviceName = "OSAICore"
$installDir = Join-Path $env:ProgramFiles "OS AI Core"
$uninstaller = Join-Path $installDir "unins000.exe"
$dataRoot = Join-Path $env:ProgramData "OS AI Core"

function Wait-HttpReady {
    param([string]$Url)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 2
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Url"
}

if (-not (Test-Path $InstallerPath)) {
    throw "Installer not found: $InstallerPath"
}

try {
    $install = Start-Process -FilePath $InstallerPath `
        -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=`"`"" `
        -Wait -PassThru
    if ($install.ExitCode -ne 0) {
        throw "Installer exited with code $($install.ExitCode)."
    }

    $service = Get-Service -Name $serviceName
    $service.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))

    $serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($serviceInfo.StartName -notin @("NT AUTHORITY\LocalService", "NT AUTHORITY\LOCAL SERVICE")) {
        throw "Service is not running under LocalService: $($serviceInfo.StartName)"
    }
    if ($serviceInfo.StartMode -ne "Auto") {
        throw "Service start mode is not Automatic: $($serviceInfo.StartMode)"
    }

    $health = Wait-HttpReady -Url "http://127.0.0.1:8765/healthz"
    if ($health.status -ne "alive") {
        throw "Unexpected health response."
    }

    $ready = Wait-HttpReady -Url "http://127.0.0.1:8765/readyz"
    if (-not $ready.ready) {
        throw "Local installer runtime did not report ready."
    }
    if ($ready.network_mode -ne "offline") {
        throw "Installer must default to offline dependency mode."
    }

    $dashboard = Invoke-WebRequest -Uri "http://127.0.0.1:8765/" -TimeoutSec 5
    if ($dashboard.Content -notmatch "Local Operator Dashboard") {
        throw "Installed runtime did not serve the operator dashboard."
    }
    if ($dashboard.Content -match "DEMO ONLY") {
        throw "Installer must not expose the demo dashboard."
    }

    $dbPath = Join-Path $dataRoot "data\osai.sqlite3"
    if (-not (Test-Path $dbPath)) {
        throw "Installed service did not create its SQLite database under ProgramData."
    }

    Write-Output "WINDOWS_INSTALLER_SMOKE_PASS"
}
finally {
    if (Test-Path $uninstaller) {
        $uninstall = Start-Process -FilePath $uninstaller `
            -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" `
            -Wait -PassThru
        if ($uninstall.ExitCode -ne 0) {
            Write-Warning "Uninstaller exited with code $($uninstall.ExitCode)."
        }
    }
}

Start-Sleep -Seconds 2
if ($null -ne (Get-Service -Name $serviceName -ErrorAction SilentlyContinue)) {
    throw "Service still exists after uninstall."
}
if (-not (Test-Path $dataRoot)) {
    throw "ProgramData should be preserved after uninstall for recovery."
}
