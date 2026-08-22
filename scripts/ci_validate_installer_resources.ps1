[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedSourceRef
)

$ErrorActionPreference = 'Stop'
$resolvedInstaller = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $resolvedInstaller -PathType Leaf)) {
    throw "Instalador ausente: $resolvedInstaller"
}
if ($ExpectedSourceRef -notmatch '^[0-9a-fA-F]{40}$') {
    throw 'ExpectedSourceRef deve ser um SHA Git completo de 40 caracteres.'
}

$assemblyBytes = [System.IO.File]::ReadAllBytes($resolvedInstaller)
$assembly = [System.Reflection.Assembly]::Load($assemblyBytes)
$resourceName = 'Nexo.Install-Nexo.ps1'
$stream = $assembly.GetManifestResourceStream($resourceName)
if ($null -eq $stream) {
    throw "Recurso obrigatório ausente do instalador: $resourceName"
}
try {
    $reader = New-Object System.IO.StreamReader(
        $stream,
        [System.Text.Encoding]::UTF8,
        $true
    )
    try {
        $scriptText = $reader.ReadToEnd()
    }
    finally {
        $reader.Dispose()
    }
}
finally {
    $stream.Dispose()
}

$sourceMatch = [regex]::Match(
    $scriptText,
    '(?m)^\s*\[string\]\$SourceRef\s*=\s*''([^'']*)'''
)
if (-not $sourceMatch.Success) {
    throw 'O recurso do instalador não declara SourceRef.'
}
$embeddedSourceRef = $sourceMatch.Groups[1].Value
if ($embeddedSourceRef -ne $ExpectedSourceRef) {
    throw "SourceRef incorporado diverge do commit esperado: $embeddedSourceRef"
}
foreach ($placeholder in @('__NEXO_GIT_REF__', '__NEXO_REPOSITORY_URL__')) {
    if ($scriptText.Contains($placeholder)) {
        throw "Placeholder não resolvido no instalador: $placeholder"
    }
}

[ordered]@{
    ok = $true
    installer = $resolvedInstaller
    source_ref = $embeddedSourceRef.ToLowerInvariant()
    resource = $resourceName
} | ConvertTo-Json -Compress
