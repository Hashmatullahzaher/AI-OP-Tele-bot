param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
$serviceName = "OSAICore"
$installDir = Join-Path $env:ProgramFiles "OS AI Core"
$uninstaller = Join-Path $installDir "unins000.exe"
$dataRoot = Join-Path $env:ProgramData "OS AI Core"
$installLog = Join-Path $dataRoot "logs\install.log"

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

    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        if (Test-Path $installLog) {
            Write-Output "--- OS AI Core install.log ---"
            Get-Content -LiteralPath $installLog
            Write-Output "--- end install.log ---"
        }
        throw "Installer completed but did not register service '$serviceName'."
    }
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

    $setupPage = Invoke-WebRequest -Uri "http://127.0.0.1:8765/setup" -TimeoutSec 5
    if ($setupPage.Content -notmatch "OS AI Core Setup") {
        throw "Installed runtime did not serve the Setup Wizard."
    }
    $csrfMatch = [regex]::Match($setupPage.Content, 'const csrf="([^"]+)"')
    if (-not $csrfMatch.Success) {
        throw "Setup Wizard did not expose its in-memory CSRF token."
    }
    $csrf = $csrfMatch.Groups[1].Value
    $accessPath = Join-Path $dataRoot "config\setup-access.token"
    if (-not (Test-Path $accessPath)) {
        throw "Setup administrator access token was not created."
    }
    $setupAccess = (Get-Content -Raw -LiteralPath $accessPath).Trim()
    if ($setupAccess.Length -lt 40) {
        throw "Setup administrator access token is invalid."
    }
    $setupHeaders = @{
        "X-OSAI-CSRF" = $csrf
        "X-OSAI-Setup-Access" = $setupAccess
        "Origin" = "http://127.0.0.1:8765"
    }

    $excel = Invoke-RestMethod -Method Post `
        -Uri "http://127.0.0.1:8765/api/setup/excel/init" `
        -Headers $setupHeaders -ContentType "application/json" -Body "{}" -TimeoutSec 10
    if (-not $excel.managed_excel_exists) {
        throw "Setup Wizard did not initialize the managed Excel workbook."
    }

    $fakeTelegram = "123456789:abcdefghijklmnopqrstuvwxyz_ABCDE"
    $fakeOpenAI = "sk-test-abcdefghijklmnopqrstuvwxyz"
    $settings = @{
        excel_enabled = $true
        excel_workbook_path = [string]$excel.managed_excel_path
        telegram_enabled = $true
        telegram_bot_alias = "primary-bot"
        llm_provider = "openai"
        llm_model = "approved-model"
        local_llm_base_url = $null
    }
    $setupBody = @{
        settings = $settings
        telegram_bot_token = $fakeTelegram
        openai_api_key = $fakeOpenAI
        clear_telegram_token = $false
        clear_openai_api_key = $false
    } | ConvertTo-Json -Depth 5

    $saved = Invoke-RestMethod -Method Post `
        -Uri "http://127.0.0.1:8765/api/setup" `
        -Headers $setupHeaders -ContentType "application/json" -Body $setupBody -TimeoutSec 10
    if (-not $saved.telegram_token_configured -or -not $saved.openai_key_configured) {
        throw "Setup Wizard did not persist credential state."
    }
    if ($saved.telegram_connector_active -or $saved.llm_connector_active) {
        throw "Setup Wizard must not claim live connectors are active."
    }

    $settingsPath = Join-Path $dataRoot "config\settings.json"
    $secretsPath = Join-Path $dataRoot "config\secrets.dpapi.json"
    if (-not (Test-Path $settingsPath) -or -not (Test-Path $secretsPath)) {
        throw "Setup Wizard did not persist configuration files."
    }
    $settingsRaw = Get-Content -Raw -LiteralPath $settingsPath
    $secretsRaw = Get-Content -Raw -LiteralPath $secretsPath
    if ($settingsRaw.Contains($fakeTelegram) -or $settingsRaw.Contains($fakeOpenAI) `
        -or $secretsRaw.Contains($fakeTelegram) -or $secretsRaw.Contains($fakeOpenAI)) {
        throw "A setup credential was persisted in plaintext."
    }

    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/setup/status" `
        -Headers @{ "X-OSAI-Setup-Access" = $setupAccess } -TimeoutSec 5
    if (-not $status.telegram_token_configured -or -not $status.openai_key_configured) {
        throw "Setup status did not report stored credential state."
    }

    Write-Output "WINDOWS_SETUP_WIZARD_SMOKE_PASS"
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
