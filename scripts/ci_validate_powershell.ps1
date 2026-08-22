[CmdletBinding()]
param(
    [string[]]$SourceDirectory = @('scripts', 'installer\windows')
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$files = @()
foreach ($directory in $SourceDirectory) {
    $resolved = Join-Path $repoRoot $directory
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "Diretório PowerShell ausente: $resolved"
    }
    $files += Get-ChildItem -LiteralPath $resolved -Filter '*.ps1' -File
}
if ($files.Count -eq 0) {
    throw 'Nenhum script PowerShell foi encontrado para validar.'
}

$problems = @()
foreach ($file in $files) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    foreach ($parseError in $parseErrors) {
        $problems += [ordered]@{
            file = $file.FullName
            line = $parseError.Extent.StartLineNumber
            column = $parseError.Extent.StartColumnNumber
            message = $parseError.Message
        }
    }
}

if ($problems.Count -gt 0) {
    $problems | ConvertTo-Json -Depth 4 | Write-Error
    throw "Foram encontrados $(($problems | Measure-Object).Count) erros de sintaxe PowerShell."
}

[ordered]@{
    ok = $true
    files = ($files | Measure-Object).Count
} | ConvertTo-Json -Compress
