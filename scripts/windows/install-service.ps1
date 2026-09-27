param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$serviceName = "OSAICore"
$displayName = "OS AI Core"
$serviceExe = Join-Path $InstallDir "OS-AI-Core-Service.exe"
$dataRoot = Join-Path $env:ProgramData "OS AI Core"
$logDir = Join-Path $dataRoot "logs"
$logPath = Join-Path $logDir "install.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-InstallLog {
    param([string]$Message)
    $timestamp = [DateTime]::UtcNow.ToString("o")
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$timestamp $Message"
}

function Assert-CimServiceResult {
    param(
        [Parameter(Mandatory = $true)]
        $Result,
        [Parameter(Mandatory = $true)]
        [string]$Operation
    )
    if ($null -eq $Result -or [int]$Result.ReturnValue -ne 0) {
        $code = if ($null -eq $Result) { "null" } else { [string]$Result.ReturnValue }
        throw "$Operation failed with Win32_Service return code $code."
    }
}

try {
    Write-InstallLog "install_begin"
    if (-not (Test-Path $serviceExe)) {
        throw "Service executable not found: $serviceExe"
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "data") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "config") | Out-Null

    # Keep runtime state private to administrators, SYSTEM, and the low-privilege service account.
    & icacls.exe $dataRoot "/inheritance:r" "/grant:r" `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-544:(OI)(CI)F" `
        "*S-1-5-19:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply ProgramData ACLs (exit $LASTEXITCODE)."
    }
    Write-InstallLog "programdata_acl_ok"

    # PathName is passed as a typed CIM value rather than a shell command line, avoiding
    # quoting ambiguity for Program Files paths. The service account is the built-in
    # LocalService identity and therefore has no password to embed or persist.
    $binPath = '"' + $serviceExe + '" service'
    $serviceCim = Get-CimInstance -ClassName Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if ($null -eq $serviceCim) {
        $createArgs = @{
            Name = $serviceName
            DisplayName = $displayName
            PathName = $binPath
            ServiceType = [byte]16
            ErrorControl = [byte]1
            StartMode = "Automatic"
            DesktopInteract = $false
            StartName = "NT AUTHORITY\LocalService"
            StartPassword = $null
        }
        $result = Invoke-CimMethod -ClassName Win32_Service -MethodName Create -Arguments $createArgs
        Assert-CimServiceResult -Result $result -Operation "Create service"
        Write-InstallLog "service_created"
    }
    else {
        $existing = Get-Service -Name $serviceName
        if ($existing.Status -ne "Stopped") {
            Stop-Service -Name $serviceName -Force
            $existing.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(20))
        }
        $changeArgs = @{
            DisplayName = $displayName
            PathName = $binPath
            ServiceType = [byte]16
            ErrorControl = [byte]1
            StartMode = "Automatic"
            DesktopInteract = $false
            StartName = "NT AUTHORITY\LocalService"
            StartPassword = $null
        }
        $result = Invoke-CimMethod -InputObject $serviceCim -MethodName Change -Arguments $changeArgs
        Assert-CimServiceResult -Result $result -Operation "Change service"
        Write-InstallLog "service_configured"
    }

    Set-Service -Name $serviceName -Description "OS AI Core local runtime" -StartupType Automatic

    Start-Service -Name $serviceName
    $service = Get-Service -Name $serviceName
    $service.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
    Write-InstallLog "service_running"
    Write-Output "OS AI Core service installed and running."
}
catch {
    Write-InstallLog ("install_error:" + $_.Exception.Message.Replace("`r", " ").Replace("`n", " "))
    throw
}
