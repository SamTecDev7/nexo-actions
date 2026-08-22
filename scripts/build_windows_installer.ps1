[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [string]$RepositoryUrl = '',
    [string]$SourceRef = '',
    [string]$CertificateThumbprint = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot 'installer\windows\NexoSetup.cs'
$manifest = Join-Path $repoRoot 'installer\windows\NexoSetup.manifest'
$powerShellSource = Join-Path $repoRoot 'installer\windows\Install-Nexo.ps1'
$bootstrapRoot = Join-Path $repoRoot 'installer\wsl\bootstrap-root.sh'
$installWsl = Join-Path $repoRoot 'installer\wsl\install-nexo.sh'

foreach ($required in @($source, $manifest, $powerShellSource, $bootstrapRoot, $installWsl)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Arquivo do instalador não encontrado: $required"
    }
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot 'dist\windows'
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'Compilador C# do .NET Framework não encontrado.'
}

if (-not $RepositoryUrl -and $env:GITHUB_SERVER_URL -and $env:GITHUB_REPOSITORY) {
    $RepositoryUrl = "$($env:GITHUB_SERVER_URL)/$($env:GITHUB_REPOSITORY).git"
}
if (-not $RepositoryUrl) {
    try {
        $candidate = (& git -C $repoRoot remote get-url origin 2>$null | Select-Object -First 1).Trim()
        if ($candidate) { $RepositoryUrl = $candidate }
    }
    catch { }
}
if ($RepositoryUrl -and $RepositoryUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$') {
    throw 'RepositoryUrl deve ser uma URL HTTPS simples do GitHub, sem credenciais.'
}
if (-not $SourceRef -and $env:GITHUB_SHA) {
    $SourceRef = $env:GITHUB_SHA
}
if (-not $SourceRef) {
    try {
        $SourceRef = (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
    }
    catch { }
}
if (-not $SourceRef) { $SourceRef = 'main' }
if ($SourceRef -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $SourceRef -match '\.\.') {
    throw 'SourceRef contém caracteres inválidos.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('NexoSetupBuild-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
try {
    $preparedPowerShell = Join-Path $temporaryRoot 'Install-Nexo.ps1'
    $scriptText = [System.IO.File]::ReadAllText($powerShellSource, [System.Text.Encoding]::UTF8)
    $scriptText = $scriptText.Replace('__NEXO_REPOSITORY_URL__', $RepositoryUrl)
    $scriptText = $scriptText.Replace('__NEXO_GIT_REF__', $SourceRef)
    # Windows PowerShell 5.1 needs a BOM to decode the Portuguese wizard as UTF-8.
    $utf8WithBom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($preparedPowerShell, $scriptText, $utf8WithBom)

    $exePath = Join-Path $OutputDirectory 'NexoSetup.exe'
    $iconPath = Join-Path $OutputDirectory 'Nexo.ico'
    $arguments = @(
        '/nologo',
        '/target:exe',
        '/optimize+',
        '/platform:x64',
        "/win32manifest:$manifest",
        '/reference:System.dll',
        "/resource:$preparedPowerShell,Nexo.Install-Nexo.ps1",
        "/resource:$bootstrapRoot,Nexo.bootstrap-root.sh",
        "/resource:$installWsl,Nexo.install-nexo.sh",
        "/out:$exePath",
        $source
    )
    if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
        $arguments = @("/win32icon:$iconPath") + $arguments
    }
    & $compiler $arguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        throw "Falha ao compilar NexoSetup.exe (código $LASTEXITCODE)."
    }

    $signature = $null
    if ($CertificateThumbprint) {
        $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction Stop
        $signature = Set-AuthenticodeSignature -FilePath $exePath -Certificate $certificate -HashAlgorithm SHA256
        if ($signature.Status -ne 'Valid') {
            throw "A assinatura Authenticode não ficou válida: $($signature.StatusMessage)"
        }
    }

    $hash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [ordered]@{
        ok = $true
        executable = $exePath
        bytes = (Get-Item -LiteralPath $exePath).Length
        sha256 = $hash
        repository_url_embedded = [bool]$RepositoryUrl
        source_ref_embedded = $SourceRef
        signed = [bool]$CertificateThumbprint
        signature_status = if ($signature) { [string]$signature.Status } else { 'NotSigned' }
    } | ConvertTo-Json -Compress
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot -PathType Container) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
