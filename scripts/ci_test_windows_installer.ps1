[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $repoRoot 'installer\windows\Install-Nexo.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "Instalador ausente: $installer"
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ('nexo-installer-ci-' + [Guid]::NewGuid().ToString('N'))
$resolvedTempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$resolvedTemporary = [IO.Path]::GetFullPath($temporaryRoot)
if (-not $resolvedTemporary.StartsWith($resolvedTempParent, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($resolvedTemporary) -notlike 'nexo-installer-ci-*') {
    throw "Diretório temporário fora do escopo permitido: $resolvedTemporary"
}

function Invoke-InstallerDryRun {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('Install', 'Repair', 'Uninstall')][string]$Mode,
        [string]$InstallPath = '/home/nexo/nexo-actions',
        [string]$SourceRef = '0123456789abcdef0123456789abcdef01234567',
        [int]$ExpectedExitCode = 0
    )
    $arguments = @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer,
        '-DryRun', '-NonInteractive', '-Mode', $Mode,
        '-Distro', 'Ubuntu', '-WslUser', 'nexo',
        '-InstallPath', $InstallPath, '-SourceRef', $SourceRef,
        '-NoDesktopShortcut', '-NoAutostart', '-SkipOpenAIValidation'
    )
    if ($Mode -ne 'Uninstall') {
        $arguments += @('-RepositoryUrl', 'https://github.com/example/nexo-actions.git')
    }
    $hostExecutable = (Get-Process -Id $PID).Path
    $output = (& $hostExecutable @arguments 2>&1 | Out-String)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne $ExpectedExitCode) {
        throw "Dry-run $Mode retornou $exitCode (esperado $ExpectedExitCode): $output"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

$previousLocalAppData = $env:LOCALAPPDATA
try {
    New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
    $env:LOCALAPPDATA = $temporaryRoot
    $plans = @()
    foreach ($mode in @('Install', 'Repair', 'Uninstall')) {
        $result = Invoke-InstallerDryRun -Mode $mode
        $jsonMatch = [regex]::Match($result.Output, '(?s)\{\s*"ok".*\}\s*$')
        if (-not $jsonMatch.Success) {
            throw "Dry-run $mode não terminou com um plano JSON: $($result.Output)"
        }
        $plan = $jsonMatch.Value | ConvertFrom-Json
        if (-not $plan.ok -or -not $plan.dry_run -or $plan.mode -ne $mode) {
            throw "Plano inválido para $mode."
        }
        if ($plan.provider -ne 'openai' -or $plan.local_fallback_enabled -ne $false) {
            throw "Dry-run $mode não preservou o perfil OpenAI-only."
        }
        if ($plan.desktop_shortcut -ne $false -or $plan.autostart -ne $false -or
            $plan.openai_validation -ne $false) {
            throw "Dry-run $mode ignorou switches explícitos."
        }
        $logFullPath = [IO.Path]::GetFullPath([string]$plan.log)
        if (-not $logFullPath.StartsWith($resolvedTemporary, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Dry-run gravou log fora do sandbox temporário: $logFullPath"
        }
        $plans += $plan
    }

    $invalidPath = Invoke-InstallerDryRun -Mode Install -InstallPath '/tmp/nexo' -ExpectedExitCode 1
    if ($invalidPath.Output -notmatch 'estritamente dentro de /home/nexo') {
        throw 'O instalador não rejeitou o caminho Linux fora do HOME esperado.'
    }
    foreach ($traversalPath in @('/home/nexo/project/..', '/home/nexo//project', '/home/nexo/project/')) {
        $traversal = Invoke-InstallerDryRun -Mode Install -InstallPath $traversalPath -ExpectedExitCode 1
        if ($traversal.Output -notmatch 'estritamente dentro|componentes simples') {
            throw "O instalador não rejeitou o caminho ambíguo: $traversalPath"
        }
    }
    $invalidRef = Invoke-InstallerDryRun -Mode Install -SourceRef '../main' -ExpectedExitCode 1
    if ($invalidRef.Output -notmatch 'Referência Git inválida') {
        throw 'O instalador não rejeitou uma referência Git com traversal.'
    }

    $unexpectedState = @(
        (Join-Path $temporaryRoot 'Nexo\installation.json'),
        (Join-Path $temporaryRoot 'Nexo\runtime.conf'),
        (Join-Path $temporaryRoot 'Nexo\bridge-token'),
        (Join-Path $temporaryRoot 'Nexo\bridge.pid')
    ) | Where-Object { Test-Path -LiteralPath $_ }
    if ($unexpectedState.Count -gt 0) {
        throw "Dry-run criou estado de runtime: $($unexpectedState -join ', ')"
    }

    [ordered]@{
        ok = $true
        simulation = 'native-powershell-dry-run'
        modes = @($plans | ForEach-Object { $_.mode })
        negative_cases = @(
            'install-path-boundary', 'install-path-terminal-parent',
            'install-path-duplicate-slash', 'git-ref-traversal'
        )
        touched_user_runtime = $false
    } | ConvertTo-Json -Compress
}
finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    if (Test-Path -LiteralPath $resolvedTemporary -PathType Container) {
        Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
    }
}
