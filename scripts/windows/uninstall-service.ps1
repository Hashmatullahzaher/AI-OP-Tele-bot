$ErrorActionPreference = "Stop"
$serviceName = "OSAICore"
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue

if ($null -ne $existing) {
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name $serviceName -Force
        $existing.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(20))
    }
    & sc.exe delete $serviceName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to remove the OS AI Core service."
    }
}

# ProgramData is intentionally preserved for audit/database recovery after uninstall.
Write-Output "OS AI Core service removed. ProgramData was preserved."
