param(
    [Parameter(Mandatory = $true)]
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$serviceName = "OSAICore"
$displayName = "OS AI Core"
$serviceExe = Join-Path $InstallDir "OS-AI-Core-Service.exe"
$dataRoot = Join-Path $env:ProgramData "OS AI Core"

if (-not (Test-Path $serviceExe)) {
    throw "Service executable not found: $serviceExe"
}

New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot "config") | Out-Null

# Keep runtime state private to administrators, SYSTEM, and the low-privilege service account.
& icacls.exe $dataRoot "/inheritance:r" "/grant:r" `
    "*S-1-5-18:(OI)(CI)F" `
    "*S-1-5-32-544:(OI)(CI)F" `
    "*S-1-5-19:(OI)(CI)M" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to apply ProgramData ACLs."
}

$binPath = '"' + $serviceExe + '" service'
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name $serviceName -Force
        $existing.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(20))
    }
    & sc.exe config $serviceName "binPath=" $binPath "start=" "auto" "obj=" "NT AUTHORITY\LocalService" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update the existing OS AI Core service."
    }
}
else {
    & sc.exe create $serviceName "binPath=" $binPath "start=" "auto" `
        "obj=" "NT AUTHORITY\LocalService" "DisplayName=" $displayName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the OS AI Core service."
    }
}

& sc.exe description $serviceName "OS AI Core local runtime" | Out-Null
& sc.exe failure $serviceName "reset=" "86400" "actions=" "restart/5000/restart/15000//0" | Out-Null

Start-Service -Name $serviceName
$service = Get-Service -Name $serviceName
$service.WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
Write-Output "OS AI Core service installed and running."
