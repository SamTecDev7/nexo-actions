[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [switch]$NoDesktopShortcut,
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'windows\NexoLauncher.cs'
$manifest = Join-Path $repoRoot 'windows\NexoLauncher.manifest'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Código do launcher não encontrado: $source"
}
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw "Manifesto do launcher não encontrado: $manifest"
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot 'dist\windows'
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'Compilador C# do .NET Framework não encontrado.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$iconPath = Join-Path $OutputDirectory 'Nexo.ico'
$exePath = Join-Path $OutputDirectory 'Nexo.exe'

Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 256, 256
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([System.Drawing.Color]::FromArgb(2, 5, 10))
$center = 128.0
for ($ring = 6; $ring -ge 1; $ring--) {
    $radius = 38 + ($ring * 11)
    $alpha = 20 + ($ring * 13)
    $pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb($alpha, 50, 225, 255)), 2
    try {
        $graphics.DrawEllipse($pen, $center - $radius, $center - $radius, $radius * 2, $radius * 2)
    }
    finally { $pen.Dispose() }
}
$outer = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(75, 32, 210, 255))
$inner = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(220, 130, 252, 255))
try {
    $graphics.FillEllipse($outer, 58, 58, 140, 140)
    $graphics.FillEllipse($inner, 91, 91, 74, 74)
}
finally {
    $outer.Dispose()
    $inner.Dispose()
    $graphics.Dispose()
}
$icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [System.IO.File]::Create($iconPath)
try { $icon.Save($stream) }
finally {
    $stream.Dispose()
    $icon.Dispose()
    $bitmap.Dispose()
}

$arguments = @(
    '/nologo',
    '/target:winexe',
    '/optimize+',
    '/platform:x64',
    "/win32icon:$iconPath",
    "/win32manifest:$manifest",
    '/reference:System.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    "/out:$exePath",
    $source
)
& $compiler $arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    throw "Falha ao compilar o Nexo.exe (código $LASTEXITCODE)."
}

if ($BuildOnly) {
    [ordered]@{
        ok = $true
        executable = $exePath
        installed_executable = $null
        desktop_shortcut = $null
        start_menu_shortcut = $null
        bytes = (Get-Item -LiteralPath $exePath).Length
        build_only = $true
    } | ConvertTo-Json -Compress
    return
}

$installRoot = Join-Path $env:LOCALAPPDATA 'Nexo'
$installedExe = Join-Path $installRoot 'Nexo.exe'
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
$runningLaunchers = @(
    Get-Process -Name Nexo -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -eq $installedExe } catch { $false }
    }
)
foreach ($launcher in $runningLaunchers) {
    Stop-Process -Id $launcher.Id -Force
}
if ($runningLaunchers.Count -gt 0) {
    Start-Sleep -Milliseconds 450
}
try {
    Copy-Item -LiteralPath $exePath -Destination $installedExe -Force
}
finally {
    if ($runningLaunchers.Count -gt 0 -and (Test-Path -LiteralPath $installedExe -PathType Leaf)) {
        Start-Process -FilePath $installedExe -WorkingDirectory $installRoot
    }
}

$shortcutPath = $null
$startMenuShortcut = $null
if (-not $NoDesktopShortcut) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'Nexo.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $installedExe
    $shortcut.WorkingDirectory = $installRoot
    $shortcut.IconLocation = "$installedExe,0"
    $shortcut.Description = 'Abrir o Nexo — assistente pessoal'
    $shortcut.Save()

    $programs = [Environment]::GetFolderPath('Programs')
    $startMenuShortcut = Join-Path $programs 'Nexo.lnk'
    $startShortcut = $shell.CreateShortcut($startMenuShortcut)
    $startShortcut.TargetPath = $installedExe
    $startShortcut.WorkingDirectory = $installRoot
    $startShortcut.IconLocation = "$installedExe,0"
    $startShortcut.Description = 'Abrir o Nexo — assistente pessoal'
    $startShortcut.Save()
}

[ordered]@{
    ok = $true
    executable = $exePath
    installed_executable = $installedExe
    desktop_shortcut = $shortcutPath
    start_menu_shortcut = $startMenuShortcut
    bytes = (Get-Item -LiteralPath $exePath).Length
    build_only = $false
} | ConvertTo-Json -Compress
