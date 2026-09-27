param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [string]$Label = "app"
)
# Start OS AI Assistant headless in an isolated profile, check its pages and
# APIs, then stop it through the same Quit endpoint the page uses.
$ErrorActionPreference = "Stop"
$root = Join-Path $env:RUNNER_TEMP ("desktop-smoke-" + [guid]::NewGuid())
$env:OSAI_DESKTOP_HOME = Join-Path $root "home"
$env:OSAI_DESKTOP_DATA = Join-Path $root "data"
New-Item -ItemType Directory -Force -Path $env:OSAI_DESKTOP_DATA | Out-Null
Copy-Item "deploy\bot\sample\expenses.csv" $env:OSAI_DESKTOP_DATA
$portFile = Join-Path $env:OSAI_DESKTOP_HOME "config\port.txt"

$proc = Start-Process -FilePath $Exe -ArgumentList "serve", "--no-browser" -PassThru
try {
    $port = $null
    for ($i = 0; $i -lt 60; $i++) {
        if ($proc.HasExited) { throw "$Label exited early with code $($proc.ExitCode)" }
        if (Test-Path $portFile) {
            $port = [int](Get-Content -Raw -LiteralPath $portFile)
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2
                if ($health.product -eq "os-ai-assistant") { break }
            } catch {}
        }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $port) { throw "$Label did not start" }

    $page = (Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 5).Content
    if ($page -notmatch "OS AI Assistant") { throw "$Label did not serve its page" }
    if ($page -notmatch 'const TOKEN = "([^"]+)"') { throw "$Label page has no session token" }
    $headers = @{ "X-OSAI-Token" = $Matches[1] }

    $status = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/status" -Headers $headers `
        -ContentType "application/json" -Body "{}"
    if ($status.tables.Count -lt 1) { throw "$Label did not discover the sample CSV" }

    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/status" -ContentType "application/json" -Body "{}"
        throw "$Label accepted a request without the session token"
    } catch [Microsoft.PowerShell.Commands.HttpResponseException] {
        if ($_.Exception.Response.StatusCode.value__ -ne 403) { throw }
    }

    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/quit" -Headers $headers `
        -ContentType "application/json" -Body "{}" | Out-Null
    if (-not $proc.WaitForExit(15000)) { throw "$Label did not stop after Quit" }
    Write-Output "DESKTOP_SMOKE_PASS ($Label, port $port, tables $($status.tables.Count))"
}
finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
