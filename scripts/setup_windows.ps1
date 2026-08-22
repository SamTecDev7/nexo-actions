[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$BindAddress = '',
    [string]$Distro = 'Ubuntu',
    [string]$WslUser = '',
    [string]$WslProject = '',
    [string]$WslPython = '',
    [string]$WindowsPython = '',
    [switch]$NoAutostart,
    [switch]$NoDesktopShortcut
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$securityFunctions = Join-Path $PSScriptRoot 'installer_security.ps1'
if (-not (Test-Path -LiteralPath $securityFunctions -PathType Leaf)) {
    throw "Funções de segurança do instalador ausentes: $securityFunctions"
}
. $securityFunctions
$sourceServer = Join-Path $repoRoot 'nexo_actions\windows_bridge\server.py'
$sourceProxy = Join-Path $repoRoot 'nexo_actions\windows_bridge\proxy.py'
$sourceObservations = Join-Path $repoRoot 'nexo_actions\windows_bridge\observations.py'
$sourceLauncher = Join-Path $repoRoot 'scripts\launch_ui.ps1'
if (-not (Test-Path -LiteralPath $sourceServer -PathType Leaf)) {
    throw "Servidor do bridge não encontrado: $sourceServer"
}

$installRoot = Join-Path $env:LOCALAPPDATA 'Nexo'
$bridgeRoot = Join-Path $installRoot 'bridge'
$tokenPath = Join-Path $installRoot 'bridge-token'
$serverPath = Join-Path $bridgeRoot 'server.py'
$proxyPath = Join-Path $bridgeRoot 'proxy.py'
$observationsPath = Join-Path $bridgeRoot 'observations.py'
$launcherPath = Join-Path $installRoot 'launch-ui.ps1'
$logPath = Join-Path $installRoot 'bridge.log'
$pidPath = Join-Path $installRoot 'bridge.pid'
$taskName = 'Nexo Windows Bridge'
$runtimePath = Join-Path $installRoot 'runtime.conf'

if (-not $BindAddress) { $BindAddress = '127.0.0.1' }
if ($BindAddress -notin @('127.0.0.1', 'localhost')) {
    throw 'O Windows Bridge só pode escutar em loopback (127.0.0.1 ou localhost).'
}

if (-not $WslProject) {
    $unc = [regex]::Match(
        [string]$repoRoot,
        '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.+)$',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if ($unc.Success) {
        $Distro = $unc.Groups[1].Value
        $WslProject = '/' + ($unc.Groups[2].Value -replace '\\', '/')
    }
}
if (-not $WslPython -and $WslProject -match '^/home/([^/]+)/') {
    $WslPython = "/home/$($matches[1])/.venvs/ovos/bin/python"
}
if (-not $WslUser) {
    $projectUser = if ($WslProject -match '^/home/([a-z_][a-z0-9_-]{0,31})(?:/|$)') { $matches[1] } else { '' }
    $pythonUser = if ($WslPython -match '^/home/([a-z_][a-z0-9_-]{0,31})(?:/|$)') { $matches[1] } else { '' }
    if ($projectUser -and $pythonUser -and $projectUser -ne $pythonUser) {
        throw 'WslProject e WslPython pertencem a usuarios WSL diferentes.'
    }
    $WslUser = if ($projectUser) { $projectUser } else { $pythonUser }
}
if (-not $WslUser) {
    throw 'Informe -WslUser ao instalar fora de um caminho UNC do WSL.'
}
if (-not $WslProject -or -not $WslPython) {
    throw 'Informe -WslProject e -WslPython ao instalar fora de um caminho UNC do WSL.'
}
if ($WslUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$') {
    throw 'O usuario WSL contem caracteres invalidos.'
}
foreach ($homePath in @($WslProject, $WslPython)) {
    if ($homePath -match '^/home/([^/]+)(?:/|$)' -and $matches[1] -cne $WslUser) {
        throw "O caminho $homePath nao pertence ao usuario WSL '$WslUser'."
    }
}
foreach ($runtimeValue in @($Distro, $WslUser, $WslProject, $WslPython)) {
    if ($runtimeValue -match '[\x0A\x0D=]') { throw 'Configuração WSL contém caractere inválido.' }
}

New-Item -ItemType Directory -Path $bridgeRoot -Force | Out-Null
Copy-Item -LiteralPath $sourceServer -Destination $serverPath -Force
Copy-Item -LiteralPath $sourceProxy -Destination $proxyPath -Force
Copy-Item -LiteralPath $sourceObservations -Destination $observationsPath -Force
Copy-Item -LiteralPath $sourceLauncher -Destination $launcherPath -Force

$shortcutPath = $null
if (-not $NoDesktopShortcut) {
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopPath 'Nexo.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $launcherPath
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.Description = 'Abrir a interface holográfica do Nexo'
    $shortcut.Save()
}
else {
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nexo.lnk') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'Nexo.lnk') -Force -ErrorAction SilentlyContinue
}

if ($WindowsPython) {
    if (-not (Test-Path -LiteralPath $WindowsPython -PathType Leaf)) {
        throw "Python do Windows não encontrado em $WindowsPython"
    }
    $python = [IO.Path]::GetFullPath($WindowsPython)
}
else {
    $pythonCommand = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1
    if (-not $pythonCommand) {
        throw 'Python do Windows não foi encontrado. Instale Python 3.11 ou superior.'
    }
    $python = $pythonCommand.Source
}
$versionCheck = & $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python do Windows 3.11+ é obrigatório; executável incompatível: $python"
}
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    $pythonw = $python
}

if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
    $existingToken = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
    if ($existingToken -notmatch '^[a-fA-F0-9]{64}$') {
        Remove-Item -LiteralPath $tokenPath -Force
    }
}
if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    $token = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $tokenTemporary = Join-Path $installRoot ('bridge-token.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllText($tokenTemporary, $token, $utf8NoBom)
        Set-NexoPrivateFileAcl -Path $tokenTemporary | Out-Null
        Move-Item -LiteralPath $tokenTemporary -Destination $tokenPath -Force
    }
    finally {
        Remove-Item -LiteralPath $tokenTemporary -Force -ErrorAction SilentlyContinue
    }
}
Set-NexoPrivateFileAcl -Path $tokenPath | Out-Null

$runtimeLines = @(
    "distro=$Distro",
    "wsl_user=$WslUser",
    "wsl_project=$WslProject",
    "wsl_python=$WslPython",
    "windows_python=$python",
    'ui_base=http://127.0.0.1:8787'
)
[System.IO.File]::WriteAllLines($runtimePath, $runtimeLines, (New-Object System.Text.UTF8Encoding($false)))

if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $oldPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($oldPidText -match '^\d+$') {
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$oldPidText" -ErrorAction SilentlyContinue
        if ($oldProcess -and $oldProcess.CommandLine -and
            $oldProcess.CommandLine -match [regex]::Escape($serverPath)) {
            Stop-Process -Id ([int]$oldPidText) -Force
            Start-Sleep -Milliseconds 500
        }
    }
}

$arguments = '"{0}" --host {1} --port {2} --token-file "{3}" --log-file "{4}" --pid-file "{5}"' -f @(
    $serverPath, $BindAddress, $Port, $tokenPath, $logPath, $pidPath
)

if (-not $NoAutostart) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $bridgeRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}
else {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $bridgeRoot -WindowStyle Hidden

$headers = @{ Authorization = 'Bearer ' + (Get-Content -LiteralPath $tokenPath -Raw).Trim() }
$health = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $body = @{ method = 'health'; params = @{} } | ConvertTo-Json -Compress
        $health = Invoke-RestMethod -Uri "http://$BindAddress`:$Port/rpc" -Method Post -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 2
        if ($health.ok) { break }
    }
    catch {
    }
}
if (-not $health -or -not $health.ok) {
    throw "O Nexo Windows Bridge não respondeu. Consulte $logPath"
}

# Build and install the native Windows launcher after the bridge is healthy.
# This replaces the legacy PowerShell shortcut with Nexo.exe.
$buildScript = Join-Path $repoRoot 'scripts\build_windows_exe.ps1'
$launcherBuild = $null
if (Test-Path -LiteralPath $buildScript -PathType Leaf) {
    $buildOutput = if ($NoDesktopShortcut) { & $buildScript -NoDesktopShortcut } else { & $buildScript }
    if ($LASTEXITCODE -ne 0) {
        throw 'Não foi possível compilar o Nexo.exe.'
    }
    $launcherBuild = $buildOutput | ConvertFrom-Json
}

[ordered]@{
    ok = $true
    python = $python
    installed_to = $bridgeRoot
    token_file = $tokenPath
    runtime_config = $runtimePath
    ui_shortcut = $shortcutPath
    executable = if ($launcherBuild) { $launcherBuild.installed_executable } else { $null }
    autostart = (-not $NoAutostart)
    bind_address = $BindAddress
    health = $health.result
} | ConvertTo-Json -Depth 5
