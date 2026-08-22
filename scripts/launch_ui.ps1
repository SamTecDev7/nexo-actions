[CmdletBinding()]
param([string]$Url = 'http://127.0.0.1:8787')

$ErrorActionPreference = 'Stop'
try {
    Invoke-RestMethod -Uri "$Url/api/state" -TimeoutSec 4 | Out-Null
}
catch {
    throw "A interface do Nexo não respondeu em $Url. Execute scripts/setup_wsl.sh no WSL."
}

$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
)
$edge = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if ($edge) {
    Start-Process -FilePath $edge -ArgumentList "--app=$Url", '--start-maximized'
    return
}

$chrome = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
if (Test-Path -LiteralPath $chrome -PathType Leaf) {
    Start-Process -FilePath $chrome -ArgumentList "--app=$Url", '--start-maximized'
    return
}

Start-Process $Url
