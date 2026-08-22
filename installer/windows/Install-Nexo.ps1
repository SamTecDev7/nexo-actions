[CmdletBinding()]
param(
    [string]$RepositoryUrl = '__NEXO_REPOSITORY_URL__',
    [string]$Distro = 'Ubuntu',
    [string]$WslUser = '',
    [string]$InstallPath = '',
    [string]$Branch = 'main',
    [string]$SourceRef = '__NEXO_GIT_REF__',
    [ValidateSet('Auto', 'Install', 'Repair', 'Uninstall')][string]$Mode = 'Auto',
    [switch]$ConfirmUninstall,
    [switch]$DryRun,
    [switch]$NonInteractive,
    [switch]$SkipOpenAIValidation,
    [switch]$NoDesktopShortcut,
    [switch]$NoAutostart,
    [switch]$ParentLockHeld,
    [string]$BootstrapRootScript = '',
    [string]$InstallWslScript = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$installRoot = Join-Path $env:LOCALAPPDATA 'Nexo'
$logRoot = Join-Path $installRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$logPath = Join-Path $logRoot ('installer-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($logPath, "Nexo Setup 2.0`r`n", $utf8NoBom)
$installerMutex = $null
$installerMutexOwned = $false

function Protect-LogText([string]$Text) {
    if ($null -eq $Text) { return '' }
    return [regex]::Replace($Text, 'sk-(?:proj-)?[A-Za-z0-9_-]{12,}', '[CREDENCIAL REDIGIDA]')
}

function Write-InstallerLog([string]$Message) {
    $safe = Protect-LogText $Message
    $line = '[{0}] {1}' -f (Get-Date -Format 'o'), $safe
    [System.IO.File]::AppendAllText($logPath, $line + "`r`n", $utf8NoBom)
}

function Write-Stage([string]$Title, [string]$Detail = '') {
    Write-Host ''
    Write-Host ('  ' + $Title) -ForegroundColor Cyan
    if ($Detail) { Write-Host ('  ' + $Detail) -ForegroundColor DarkGray }
    Write-InstallerLog ($Title + $(if ($Detail) { ': ' + $Detail } else { '' }))
}

function Read-Default([string]$Prompt, [string]$Default) {
    if ($NonInteractive) { return $Default }
    $answer = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function Read-YesNo([string]$Prompt, [bool]$Default) {
    if ($NonInteractive) { return $Default }
    $suffix = if ($Default) { 'S/n' } else { 's/N' }
    $answer = (Read-Host "$Prompt [$suffix]").Trim().ToLowerInvariant()
    if (-not $answer) { return $Default }
    return @('s', 'sim', 'y', 'yes').Contains($answer)
}

function Get-SecureLength([Security.SecureString]$Value) {
    if ($null -eq $Value) { return 0 }
    return $Value.Length
}

function Quote-NativeArgument([string]$Value) {
    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-NativeProcess {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Security.SecureString]$StandardInputSecret = $null,
        [int]$TimeoutSeconds = 1800,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = (($ArgumentList | ForEach-Object { Quote-NativeArgument ([string]$_) }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $null -ne $StandardInputSecret
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if ($null -ne $StandardInputSecret) {
        $pointer = [IntPtr]::Zero
        try {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($StandardInputSecret)
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $process.StandardInput.WriteLine($plain)
            $process.StandardInput.Close()
            $plain = $null
        }
        finally {
            if ($pointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        }
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "Tempo excedido ao executar $([IO.Path]::GetFileName($FilePath))."
    }
    $process.WaitForExit()
    $stdout = ($stdoutTask.Result -replace "`0", '').TrimEnd()
    $stderr = ($stderrTask.Result -replace "`0", '').TrimEnd()
    if ($stdout) { Write-InstallerLog $stdout }
    if ($stderr) { Write-InstallerLog $stderr }
    if (-not $Quiet) {
        if ($stdout) { Write-Host $stdout }
        if ($stderr -and $process.ExitCode -ne 0) { Write-Host $stderr -ForegroundColor DarkYellow }
    }
    $result = [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout; Error = $stderr }
    if (-not $AllowFailure -and $result.ExitCode -ne 0) {
        $detail = if ($stderr) { $stderr } elseif ($stdout) { $stdout } else { "código $($result.ExitCode)" }
        throw (Protect-LogText $detail)
    }
    return $result
}

function Convert-ToWslMountPath([string]$WindowsPath) {
    $full = [IO.Path]::GetFullPath($WindowsPath)
    if ($full -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "O bootstrap deve estar em uma unidade local do Windows: $full"
    }
    return '/mnt/' + $matches[1].ToLowerInvariant() + '/' + ($matches[2] -replace '\\', '/')
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-WindowsPython {
    $candidates = New-Object System.Collections.Generic.List[string]
    @(Get-Command python.exe -All -ErrorAction SilentlyContinue | Where-Object {
        $_.Source -notlike '*\WindowsApps\*'
    }) | ForEach-Object { [void]$candidates.Add($_.Source) }
    $roots = @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $root -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike '*\WindowsApps\*' } |
            ForEach-Object { [void]$candidates.Add($_.FullName) }
    }
    foreach ($candidate in @($candidates | Sort-Object -Unique -Descending)) {
        $compatible = Invoke-NativeProcess $candidate @(
            '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
        ) -AllowFailure -Quiet
        if ($compatible.ExitCode -eq 0) { return $candidate }
    }
    return $null
}

function Test-OpenAIKey([Security.SecureString]$Credential) {
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Credential)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $request = [Net.HttpWebRequest]::Create('https://api.openai.com/v1/models')
        $request.Method = 'GET'
        $request.Timeout = 20000
        $request.ReadWriteTimeout = 20000
        $request.Headers['Authorization'] = 'Bearer ' + $plain
        $response = $request.GetResponse()
        try { return [int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 300 }
        finally { $response.Dispose() }
    }
    catch [Net.WebException] {
        return $false
    }
    finally {
        $plain = $null
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
}

try {
    if (-not $ParentLockHeld) {
        $installerMutex = New-Object System.Threading.Mutex($false, 'Local\Nexo.WindowsSetup')
        try { $installerMutexOwned = $installerMutex.WaitOne(0, $false) }
        catch [System.Threading.AbandonedMutexException] { $installerMutexOwned = $true }
        if (-not $installerMutexOwned) {
            throw 'Outra instalação do Nexo já está em andamento.'
        }
    }
    Clear-Host
    Write-Host '  N E X O  2.0  //  INSTALADOR' -ForegroundColor Cyan
    Write-Host '  OpenAI-first para Windows 11 + WSL Ubuntu' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  Este assistente instala ou atualiza sem apagar seus dados.' -ForegroundColor DarkGray

    if ($RepositoryUrl -eq '__NEXO_REPOSITORY_URL__') { $RepositoryUrl = '' }
    if ($SourceRef -eq '__NEXO_GIT_REF__') { $SourceRef = $Branch }
    $installationPath = Join-Path $installRoot 'installation.json'
    $existingInstallation = $null
    if (Test-Path -LiteralPath $installationPath -PathType Leaf) {
        try { $existingInstallation = Get-Content -LiteralPath $installationPath -Raw | ConvertFrom-Json }
        catch { Write-InstallerLog 'installation.json inválido; usando descoberta do WSL.' }
    }
    if ($Mode -eq 'Auto') {
        if ($NonInteractive) { $Mode = 'Install' }
        else {
            Write-Host ''
            Write-Host '  1. Instalar ou atualizar' -ForegroundColor White
            Write-Host '  2. Reparar sem mudar a versão' -ForegroundColor White
            Write-Host '  3. Remover componentes ativos (preserva dados, projeto e venv)' -ForegroundColor White
            $modeChoice = (Read-Host '  Escolha [1]').Trim()
            $Mode = switch ($modeChoice) { '2' { 'Repair' } '3' { 'Uninstall' } default { 'Install' } }
        }
    }
    if ($existingInstallation) {
        if (-not $PSBoundParameters.ContainsKey('Distro') -and $existingInstallation.distro) { $Distro = [string]$existingInstallation.distro }
        if (-not $WslUser -and $existingInstallation.wsl_user) { $WslUser = [string]$existingInstallation.wsl_user }
        if (-not $InstallPath -and $existingInstallation.wsl_project) { $InstallPath = [string]$existingInstallation.wsl_project }
        if (-not $RepositoryUrl -and $existingInstallation.repository_url) { $RepositoryUrl = [string]$existingInstallation.repository_url }
    }
    $defaultUser = ($env:USERNAME.ToLowerInvariant() -replace '[^a-z0-9_-]', '-')
    if ($defaultUser -notmatch '^[a-z_]') { $defaultUser = 'nexo' }
    $Distro = Read-Default 'Distribuição WSL' $Distro
    if ($Distro -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw 'O nome da distribuição WSL é inválido.' }
    if (-not $WslUser) {
        $existingWsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
        if (Test-Path -LiteralPath $existingWsl) {
            $discoveredUser = Invoke-NativeProcess $existingWsl @('-d', $Distro, '--exec', 'sh', '-lc', 'id -un') -AllowFailure -Quiet
            if ($discoveredUser.ExitCode -eq 0 -and $discoveredUser.Output -match '^[a-z_][a-z0-9_-]{0,31}$') {
                $defaultUser = $discoveredUser.Output.Trim()
            }
        }
    }
    $WslUser = Read-Default 'Usuário Linux do Nexo' $(if ($WslUser) { $WslUser } else { $defaultUser })
    if ($WslUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { throw 'O usuário WSL contém caracteres inválidos.' }
    $InstallPath = Read-Default 'Pasta do Nexo dentro do WSL' $(if ($InstallPath) { $InstallPath } else { "/home/$WslUser/nexo-actions" })
    $homePrefix = "/home/$WslUser/"
    if (-not $InstallPath.StartsWith($homePrefix, [StringComparison]::Ordinal) -or $InstallPath.EndsWith('/')) {
        throw "A pasta deve ficar estritamente dentro de /home/$WslUser."
    }
    $installSegments = @($InstallPath.Substring($homePrefix.Length).Split('/'))
    if (
        $installSegments.Count -eq 0 -or
        @($installSegments | Where-Object {
            -not $_ -or $_ -in @('.', '..') -or $_ -notmatch '^[A-Za-z0-9._-]+$'
        }).Count -gt 0
    ) {
        throw "A pasta deve usar componentes simples e não pode conter '.', '..', barras duplicadas ou links."
    }
    $InstallPath = $homePrefix + ($installSegments -join '/')
    if ($Mode -ne 'Uninstall' -and -not $RepositoryUrl) {
        $RepositoryUrl = Read-Default 'URL HTTPS do repositório GitHub' 'https://github.com/SEU_USUARIO/nexo-actions.git'
    }
    if ($Mode -ne 'Uninstall' -and ($RepositoryUrl -notmatch '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$' -or $RepositoryUrl -match '/SEU_USUARIO/')) {
        throw 'Informe a URL HTTPS real do repositório Nexo no GitHub.'
    }
    if ($SourceRef -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $SourceRef -match '\.\.') { throw 'Referência Git inválida.' }

    $createDesktop = -not $NoDesktopShortcut
    $enableAutostart = -not $NoAutostart
    $validateOpenAI = -not $SkipOpenAIValidation
    if (-not $NonInteractive -and $Mode -ne 'Uninstall') {
        $createDesktop = Read-YesNo 'Criar atalhos na Área de Trabalho e menu Iniciar?' $true
        $enableAutostart = Read-YesNo 'Iniciar a ponte segura ao entrar no Windows?' $true
        $validateOpenAI = Read-YesNo 'Validar a chave e o acesso à OpenAI durante a instalação?' $true
    }

    if ($DryRun) {
        Write-Stage 'SIMULAÇÃO CONCLUÍDA' 'Nenhuma alteração de sistema; somente este log de simulação foi criado.'
        [ordered]@{
            ok = $true; dry_run = $true; distro = $Distro; wsl_user = $WslUser
            mode = $Mode; install_path = $InstallPath; repository_url = $RepositoryUrl; source_ref = $SourceRef
            provider = 'openai'; local_fallback_enabled = $false
            desktop_shortcut = $createDesktop; autostart = $enableAutostart
            openai_validation = $validateOpenAI; log = $logPath
        } | ConvertTo-Json -Depth 4
        exit 0
    }

    if (-not (Test-IsAdministrator)) { throw 'Execute NexoSetup.exe e aceite a solicitação de administrador.' }
    $wslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
    if ($Mode -eq 'Uninstall') {
        if (-not $ConfirmUninstall -and -not $NonInteractive) {
            $confirmation = (Read-Host '  Digite DESINSTALAR para remover o runtime e preservar seus dados').Trim()
            $ConfirmUninstall = $confirmation -eq 'DESINSTALAR'
        }
        if (-not $ConfirmUninstall) { throw 'Desinstalação cancelada: confirmação explícita ausente.' }
        Write-Stage 'REMOVENDO COMPONENTES ATIVOS' 'Configuração, memória, .env, venv, logs e checkout Git serão preservados.'
        if (Test-Path -LiteralPath $wslExe) {
            $removeUnits = @'
systemctl --user disable --now ovos-listener.service ovos-core.service ovos-audio.service ovos-phal.service ovos-messagebus.service nexo-ui.service nexo-pulse-aec.service nexo-audio-watchdog.service nexo-neural-tts.service 2>/dev/null || true
rm -f -- "$HOME/.config/systemd/user/ovos-listener.service" "$HOME/.config/systemd/user/ovos-core.service" "$HOME/.config/systemd/user/ovos-audio.service" "$HOME/.config/systemd/user/ovos-phal.service" "$HOME/.config/systemd/user/ovos-messagebus.service" "$HOME/.config/systemd/user/nexo-ui.service" "$HOME/.config/systemd/user/nexo-pulse-aec.service" "$HOME/.config/systemd/user/nexo-audio-watchdog.service" "$HOME/.config/systemd/user/nexo-neural-tts.service" "$HOME/.config/systemd/user/ovos-audio.service.d/zz-nexo-pulse-aec.conf" "$HOME/.config/systemd/user/ovos-listener.service.d/zz-nexo-pulse-aec.conf"
rmdir "$HOME/.config/systemd/user/ovos-audio.service.d" "$HOME/.config/systemd/user/ovos-listener.service.d" 2>/dev/null || true
if [[ -d "$HOME/.local/share/nexo-pulse/root" && ! -L "$HOME/.local/share/nexo-pulse/root" ]]; then
  rm -rf -- "$HOME/.local/share/nexo-pulse/root"
fi
systemctl --user daemon-reload
'@
            Invoke-NativeProcess $wslExe @('-d', $Distro, '-u', $WslUser, '--exec', 'bash', '-lc', $removeUnits) -AllowFailure -Quiet | Out-Null
        }
        Unregister-ScheduledTask -TaskName 'Nexo Windows Bridge' -Confirm:$false -ErrorAction SilentlyContinue
        $pidPath = Join-Path $installRoot 'bridge.pid'
        if (Test-Path -LiteralPath $pidPath) {
            $pidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
            if ($pidText -match '^\d+$') {
                $bridgeServer = Join-Path $installRoot 'bridge\server.py'
                $bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$pidText" -ErrorAction SilentlyContinue
                if ($bridgeProcess -and $bridgeProcess.CommandLine -and
                    $bridgeProcess.CommandLine -match [regex]::Escape($bridgeServer)) {
                    Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
                }
            }
        }
        foreach ($shortcut in @(
            (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Nexo.lnk'),
            (Join-Path ([Environment]::GetFolderPath('Programs')) 'Nexo.lnk')
        )) { Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue }
        foreach ($target in @('bridge', 'Nexo.exe', 'runtime.conf', 'bridge.pid', 'bridge-token', 'launch-ui.ps1')) {
            $resolved = Join-Path $installRoot $target
            if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
        }
        Write-Stage 'COMPONENTES ATIVOS REMOVIDOS' "Projeto, venv e dados preservados em $InstallPath e ~/.config/nexo."
        exit 0
    }
    if (-not $BootstrapRootScript -or -not (Test-Path -LiteralPath $BootstrapRootScript)) { throw 'Bootstrap WSL não foi incorporado ao instalador.' }
    if (-not $InstallWslScript -or -not (Test-Path -LiteralPath $InstallWslScript)) { throw 'Instalador WSL não foi incorporado ao instalador.' }

    Write-Host ''
    Write-Host '  Chave da API OpenAI' -ForegroundColor White
    Write-Host '  Ela será enviada somente por stdin e salva como .env com permissão 600.' -ForegroundColor DarkGray
    Write-Host '  Em uma atualização, deixe em branco para preservar a chave existente.' -ForegroundColor DarkGray
    $openAIKey = Read-Host '  OPENAI_API_KEY' -AsSecureString
    if ((Get-SecureLength $openAIKey) -eq 0 -and -not $existingInstallation) {
        throw 'Uma instalação nova exige a OPENAI_API_KEY. Em atualizações, ela pode ficar em branco.'
    }
    if ((Get-SecureLength $openAIKey) -gt 0 -and $validateOpenAI) {
        Write-Stage 'VALIDANDO OPENAI' 'A chave não será exibida nem registrada.'
        if (-not (Test-OpenAIKey $openAIKey)) { throw 'A OpenAI recusou a chave ou não respondeu. Confira a chave e a conexão.' }
    }

    Write-Stage 'PREPARANDO WSL' 'Ativando os componentes oficiais quando necessário.'
    $restartNeeded = $false
    foreach ($featureName in @('Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform')) {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $featureName
        if ($feature.State -ne 'Enabled') {
            $enabled = Enable-WindowsOptionalFeature -Online -FeatureName $featureName -All -NoRestart
            if ($enabled.RestartNeeded) { $restartNeeded = $true }
        }
    }
    if ($restartNeeded) {
        Write-InstallerLog 'Os recursos WSL foram habilitados; reinicialização necessária.'
        Write-Host ''
        Write-Host '  Reinicie o Windows e execute o mesmo NexoSetup.exe novamente.' -ForegroundColor Yellow
        Write-Host "  Log: $logPath" -ForegroundColor DarkGray
        exit 3010
    }

    if (-not (Test-Path -LiteralPath $wslExe)) { throw 'wsl.exe não foi encontrado após habilitar os componentes.' }
    $listed = Invoke-NativeProcess $wslExe @('--list', '--quiet') -AllowFailure -Quiet
    $distros = @($listed.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($distros -notcontains $Distro) {
        Write-Stage 'INSTALANDO UBUNTU' 'O Windows pode levar alguns minutos para baixar a distribuição.'
        Invoke-NativeProcess $wslExe @('--install', '--distribution', $Distro, '--no-launch') -TimeoutSeconds 2400 | Out-Null
    }

    $bootstrapWsl = Convert-ToWslMountPath $BootstrapRootScript
    $installWsl = Convert-ToWslMountPath $InstallWslScript
    $userCheck = Invoke-NativeProcess $wslExe @('-d', $Distro, '-u', 'root', '--exec', 'id', '-u', $WslUser) -AllowFailure -Quiet
    $newUser = $userCheck.ExitCode -ne 0
    $linuxPassword = $null
    if ($newUser) {
        Write-Host ''
        Write-Host "  Crie a senha sudo do usuário Linux '$WslUser'." -ForegroundColor White
        $linuxPassword = Read-Host '  Senha WSL' -AsSecureString
        if ((Get-SecureLength $linuxPassword) -lt 8) { throw 'Use uma senha WSL com pelo menos 8 caracteres.' }
    }

    Write-Stage 'CONFIGURANDO UBUNTU' 'Dependências, usuário, systemd e áudio.'
    $rootArguments = @('-d', $Distro, '-u', 'root', '--exec', 'bash', $bootstrapWsl, '--user', $WslUser)
    if ($newUser) { $rootArguments += '--password-stdin' }
    Invoke-NativeProcess $wslExe $rootArguments -StandardInputSecret $linuxPassword -TimeoutSeconds 2400 | Out-Null
    Invoke-NativeProcess $wslExe @('--terminate', $Distro) -AllowFailure -Quiet | Out-Null
    Start-Sleep -Seconds 3

    $venvPath = "/home/$WslUser/.venvs/ovos"
    $wslArguments = @(
        '-d', $Distro, '-u', $WslUser, '--exec', 'bash', $installWsl,
        '--repository-url', $RepositoryUrl, '--source-ref', $SourceRef,
        '--install-dir', $InstallPath, '--venv', $venvPath
    )
    if ($validateOpenAI) { } else { $wslArguments += '--skip-openai-validation' }
    if ($Mode -eq 'Repair') { $wslArguments += '--skip-git-update' }
    if ((Get-SecureLength $openAIKey) -gt 0) { $wslArguments += '--read-key-stdin' }
    else { $wslArguments += '--preserve-key' }
    Write-Stage 'INSTALANDO NEXO' 'OVOS, perfil OpenAI, serviços e cancelamento de eco.'
    Invoke-NativeProcess $wslExe $wslArguments -StandardInputSecret $openAIKey -TimeoutSeconds 3600 | Out-Null

    Write-Stage 'PREPARANDO O WINDOWS' 'Python local para a ponte segura de ações.'
    $windowsPython = Find-WindowsPython
    if (-not $windowsPython) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) { throw 'Python do Windows não foi encontrado e o winget não está disponível.' }
        Invoke-NativeProcess $winget.Source @(
            'install', '--exact', '--id', 'Python.Python.3.12', '--scope', 'user', '--silent',
            '--accept-package-agreements', '--accept-source-agreements'
        ) -TimeoutSeconds 1800 | Out-Null
        $windowsPython = Find-WindowsPython
        if (-not $windowsPython) { throw 'O Python foi instalado, mas ainda não foi localizado. Entre novamente no Windows e reexecute o instalador.' }
    }

    $windowsRepo = "\\wsl.localhost\$Distro" + ($InstallPath -replace '/', '\')
    $setupWindows = Join-Path $windowsRepo 'scripts\setup_windows.ps1'
    if (-not (Test-Path -LiteralPath $setupWindows -PathType Leaf)) { throw "setup_windows.ps1 não encontrado em $windowsRepo" }
    $setupArguments = @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $setupWindows,
        '-Distro', $Distro, '-WslUser', $WslUser,
        '-WslProject', $InstallPath, '-WslPython', "$venvPath/bin/python",
        '-WindowsPython', $windowsPython
    )
    if (-not $enableAutostart) { $setupArguments += '-NoAutostart' }
    if (-not $createDesktop) { $setupArguments += '-NoDesktopShortcut' }
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Invoke-NativeProcess $powerShell $setupArguments -TimeoutSeconds 900 | Out-Null

    Write-Stage 'VALIDANDO SAÚDE' 'Serviços, interface e bridge em loopback.'
    $healthCommand = @"
set -e
systemctl --user is-active ovos-messagebus.service nexo-pulse-aec.service ovos-audio.service ovos-core.service ovos-listener.service nexo-ui.service
curl -fsS --max-time 8 http://127.0.0.1:8787/api/state >/dev/null
"@
    Invoke-NativeProcess $wslExe @('-d', $Distro, '-u', $WslUser, '--exec', 'bash', '-lc', $healthCommand) -TimeoutSeconds 120 | Out-Null

    $commitResult = Invoke-NativeProcess $wslExe @('-d', $Distro, '-u', $WslUser, '--exec', 'git', '-C', $InstallPath, 'rev-parse', 'HEAD') -Quiet
    $recordedSourceRef = if ($Mode -eq 'Repair') { $commitResult.Output.Trim() } else { $SourceRef }
    $installation = [ordered]@{
        version = '2.0.1'; installed_at = (Get-Date).ToString('o')
        repository_url = $RepositoryUrl; source_ref = $recordedSourceRef; source_commit = $commitResult.Output.Trim(); distro = $Distro
        wsl_user = $WslUser; wsl_project = $InstallPath; wsl_python = "$venvPath/bin/python"
        provider = 'openai'; local_fallback_enabled = $false
        installer_log = $logPath
    }
    [IO.File]::WriteAllText($installationPath, ($installation | ConvertTo-Json -Depth 4), $utf8NoBom)

    Write-Stage 'NEXO PRONTO' 'Instalação e validação concluídas.'
    Write-Host "  Executável: $(Join-Path $installRoot 'Nexo.exe')" -ForegroundColor Green
    Write-Host '  Painel ADM: http://127.0.0.1:8787/admin' -ForegroundColor Gray
    Write-Host "  Log Windows: $logPath" -ForegroundColor DarkGray
    Write-Host "  Log WSL: /home/$WslUser/.local/state/nexo/install.log" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Diga: Nexo, vamos conversar.' -ForegroundColor Cyan
    if (-not $NonInteractive) { [void](Read-Host '  Pressione Enter para fechar') }
    exit 0
}
catch {
    $safeError = Protect-LogText $_.Exception.Message
    Write-InstallerLog ('FALHA: ' + $safeError)
    Write-Host ''
    Write-Host '  A instalação não foi concluída.' -ForegroundColor Red
    Write-Host ('  ' + $safeError) -ForegroundColor Yellow
    Write-Host "  Diagnóstico: $logPath" -ForegroundColor DarkGray
    Write-Host '  É seguro corrigir o ponto indicado e executar o instalador novamente.' -ForegroundColor DarkGray
    if (-not $NonInteractive) { [void](Read-Host '  Pressione Enter para fechar') }
    exit 1
}
finally {
    if ($installerMutexOwned -and $null -ne $installerMutex) {
        try { $installerMutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $installerMutex) { $installerMutex.Dispose() }
}
