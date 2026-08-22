Set-StrictMode -Version 2.0

function Set-NexoPrivateFileAcl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Arquivo privado ausente: $resolved"
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw 'Não foi possível identificar o SID do usuário atual.'
    }
    $sid = $identity.User
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $sid,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $resolved -AclObject $acl

    $verified = Get-Acl -LiteralPath $resolved
    if (-not $verified.AreAccessRulesProtected) {
        throw "A herança de ACL não foi removida de $resolved"
    }
    $unexpected = @($verified.Access | Where-Object {
        $_.IsInherited -or
        $_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value
    })
    $ownerSid = $verified.Owner
    try {
        $ownerSid = ([System.Security.Principal.NTAccount]$verified.Owner).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
    }
    if ($unexpected.Count -gt 0 -or $ownerSid -ne $sid.Value) {
        throw "A ACL privada não pôde ser verificada em $resolved"
    }
    return $verified
}
