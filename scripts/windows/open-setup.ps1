$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $PSCommandPath + '"')
    )
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments | Out-Null
    exit 0
}

$tokenPath = Join-Path $env:ProgramData "OS AI Core\config\setup-access.token"
if (-not (Test-Path $tokenPath)) {
    throw "OS AI Core setup access token is unavailable. Ensure the OSAICore service is running."
}

$token = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
if ($token.Length -lt 40) {
    throw "OS AI Core setup access token is invalid."
}

$escaped = [Uri]::EscapeDataString($token)
$url = "http://127.0.0.1:8765/setup#access=$escaped"
Start-Process -FilePath "explorer.exe" -ArgumentList $url | Out-Null
