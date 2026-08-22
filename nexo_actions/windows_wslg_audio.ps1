param(
    [ValidateSet("repair", "status", "capture-status")]
    [string]$Action = "repair"
)

$ErrorActionPreference = "Stop"
$expectedPath = Join-Path $env:ProgramFiles "WSL\msrdc.exe"

function Get-WslgClient {
    @(Get-CimInstance Win32_Process -Filter "Name='msrdc.exe'" | Where-Object {
        $_.ExecutablePath -eq $expectedPath -and $_.CommandLine -match '(?i)(^|\s)/wslg(\s|$)'
    })
}

function Get-CaptureEndpoint {
    @(Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction Stop | Where-Object {
        $_.Status -eq "OK" -and $_.InstanceId -match '(?i)\\\{0\.0\.1\.'
    })
}

try {
    $clients = @(Get-WslgClient)
    $captureEndpoints = @(Get-CaptureEndpoint)
    if ($Action -eq "capture-status") {
        [ordered]@{
            ok = $true
            available = $captureEndpoints.Count -gt 0
            endpoints = @($captureEndpoints | ForEach-Object { [string]$_.FriendlyName })
        } | ConvertTo-Json -Compress
        exit 0
    }
    if ($Action -eq "status") {
        [ordered]@{
            ok = $true
            running = $clients.Count -gt 0
            process_ids = @($clients | ForEach-Object { [int]$_.ProcessId })
            capture_available = $captureEndpoints.Count -gt 0
            capture_endpoints = @($captureEndpoints | ForEach-Object { [string]$_.FriendlyName })
        } | ConvertTo-Json -Compress
        exit 0
    }

    if ($captureEndpoints.Count -lt 1) {
        throw "Nenhum microfone ativo foi encontrado no Windows; aguardando a mesa ou placa voltar"
    }

    if ($clients.Count -lt 1) {
        throw "Cliente de áudio WSLg não encontrado no Windows"
    }

    # Preserve the exact WSLg session identifiers before terminating only the
    # internal RDP client owned by WSL.  No ordinary Remote Desktop session is
    # selected by this filter.
    $commandLine = [string]$clients[0].CommandLine
    $oldIds = @($clients | ForEach-Object { [int]$_.ProcessId })
    foreach ($client in $clients) {
        Stop-Process -Id ([int]$client.ProcessId) -Force
    }

    $replacement = $null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 150
        $replacement = @(Get-WslgClient | Where-Object { $oldIds -notcontains [int]$_.ProcessId }) | Select-Object -First 1
        if ($replacement) { break }
    }

    $launchedManually = $false
    if (-not $replacement) {
        # Start-Process completes the WSLg dynamic-channel handshake more
        # reliably than Win32_Process.Create. Preserve only this session's
        # identifiers from the original internal RDP client.
        $vSwitch = [regex]::Match($commandLine, '(?i)/v:[^\s"]+').Value
        $hvSwitch = [regex]::Match($commandLine, '(?i)/hvsocketserviceid:[^\s"]+').Value
        $sharedSwitch = [regex]::Match($commandLine, '(?i)/wslgsharedmemorypath:[^\s"]+').Value
        if (-not $vSwitch -or -not $hvSwitch -or -not $sharedSwitch) {
            throw "Não foi possível preservar os identificadores da sessão WSLg"
        }
        $rdpFile = Join-Path $env:ProgramFiles "WSL\wslg.rdp"
        $started = Start-Process -FilePath $expectedPath -ArgumentList @(
            "/wslg", "/silent", $vSwitch, $hvSwitch,
            "/plugin:WSLDVC_PACKAGE", $sharedSwitch, $rdpFile
        ) -WindowStyle Hidden -PassThru
        $launchedManually = $true
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 200
            $replacement = @(Get-WslgClient | Where-Object {
                [int]$_.ProcessId -eq [int]$started.Id
            }) | Select-Object -First 1
            if ($replacement) { break }
        }
    }

    if (-not $replacement) {
        throw "O cliente WSLg não voltou após a recuperação"
    }

    [ordered]@{
        ok = $true
        restarted = $true
        old_process_ids = $oldIds
        process_id = [int]$replacement.ProcessId
        launched_manually = $launchedManually
    } | ConvertTo-Json -Compress
    exit 0
}
catch {
    [ordered]@{
        ok = $false
        error = $_.Exception.Message
    } | ConvertTo-Json -Compress
    exit 1
}
