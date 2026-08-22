# Publicação e artefatos

Este documento descreve o pipeline existente em `.github/workflows` para o
repositório [SamTecDev7/nexo-actions](https://github.com/SamTecDev7/nexo-actions).

## O que a CI valida

`.github/workflows/ci.yml` roda em todo push, pull request e acionamento manual.
Ele também pode ser reutilizado por outro workflow.

O job Linux executa em Python 3.10, 3.11 e 3.12:

1. instala as dependências de teste e o pacote em modo editável;
2. recusa arquivos de credencial rastreados e padrões prováveis de chave;
3. confere se as versões do pacote, do launcher e do instalador são iguais;
4. compila fontes Python;
5. valida a sintaxe dos scripts shell do instalador WSL;
6. executa os testes unitários e de integração.

Depois dos testes, o job Windows:

1. valida a sintaxe de todos os scripts PowerShell de setup e build;
2. compila `dist/windows/Nexo.exe` sem instalá-lo na máquina da CI;
3. compila `dist/windows/NexoSetup.exe` com o SHA atual incorporado e confere
   essa referência;
4. verifica cabeçalho PE, tamanho mínimo e ausência de padrões de segredo;
5. gera `dist/windows/ReleaseManifest.json` com commit-fonte, tamanho e SHA-256;
6. publica os três arquivos como artefato de CI por 14 dias.

Os testes de CI não usam uma `OPENAI_API_KEY` real nem substituem testes de voz,
tela, áudio ou ações em uma sessão Windows interativa.

As actions externas são fixadas por SHA completo e o checkout não persiste a
credencial Git. O Dependabot apenas propõe atualizações; cada mudança de SHA deve
passar pela revisão e pela CI antes de ser aceita.

## Preparar uma versão

A versão precisa ser idêntica em:

- `setup.py`, campo `version`;
- `windows/NexoLauncher.cs`, atributo `AssemblyInformationalVersion`;
- `installer/windows/NexoSetup.cs`, atributo `AssemblyFileVersion`;
- tag Git, no formato `vMAJOR.MINOR.PATCH`.

Antes de criar a tag:

```bash
python scripts/ci_check_repository.py
python scripts/ci_validate_version.py
python -m compileall -q nexo_actions scripts tests
python -m unittest discover -s tests -q
```

No Windows, faça também um build limpo dos executáveis:

```powershell
$sourceCommit = (git rev-parse HEAD).Trim()
pwsh -NoProfile -File .\scripts\build_windows_exe.ps1 `
  -OutputDirectory .\dist\windows -NoDesktopShortcut -BuildOnly
pwsh -NoProfile -File .\scripts\build_windows_installer.ps1 `
  -RepositoryUrl 'https://github.com/SamTecDev7/nexo-actions.git' `
  -SourceRef $sourceCommit
python .\scripts\ci_validate_artifacts.py `
  --directory .\dist\windows --require-launcher --require-installer `
  --source-commit $sourceCommit `
  --manifest .\dist\windows\ReleaseManifest.json
```

Em um PowerShell elevado, valide também o wizard sem alterar WSL ou serviços. A
simulação cria apenas seu log em `%LOCALAPPDATA%\Nexo\logs`:

```powershell
.\dist\windows\NexoSetup.exe -DryRun -NonInteractive `
  -RepositoryUrl 'https://github.com/SamTecDev7/nexo-actions.git' `
  -SourceRef $sourceCommit
if ($LASTEXITCODE -ne 0) { throw "Dry-run falhou: $LASTEXITCODE" }
```

Antes de uma release pública, use ainda uma conta ou VM Windows 11 descartável
para testar instalação sem WSL prévio, reboot/continuação quando solicitado,
abertura da UI e bridge, atualização para o commit da release, reparo sem mudar
o checkout e desinstalação seletiva. Áudio, microfone e ações de tela exigem uma
sessão interativa e não são cobertos pelo runner comum do GitHub Actions.

Revise `CHANGELOG.md` e confirme que `.env` e logs não estão rastreados. Um build
local não deve ser anexado manualmente a uma release se ele não passou pelo
mesmo commit/tag.

## Criar a release

Crie e envie uma tag anotada somente em um commit aprovado na branch principal:

```bash
git tag -a v2.0.0 -m "Nexo 2.0.0"
git push origin v2.0.0
```

`.github/workflows/release.yml` observa tags com três segmentos e aceita somente
o formato exato `vX.Y.Z`. Ele reutiliza toda a CI, confere a tag contra os
fontes, baixa os binários que acabaram de ser validados e verifica novamente o
commit-fonte, os tamanhos e os hashes antes de criar a GitHub Release. O workflow
se recusa a sobrescrever uma release já existente.

O ambiente `github-release` pode receber regras de proteção no GitHub, como
aprovação manual. O workflow pede apenas `contents: write` no job de publicação;
os demais jobs usam leitura.

## Conteúdo publicado

Cada release contém:

- `Nexo.exe`: launcher nativo da interface;
- `NexoSetup.exe`: instalador interativo Windows + WSL;
- `ReleaseManifest.json`: commit-fonte, tamanho e SHA-256 calculados no build;
- `SHA256SUMS.txt`: hashes recalculados imediatamente antes da publicação.

Os binários não são assinados digitalmente na versão 2.0. O checksum detecta
alteração em relação ao arquivo publicado, mas não substitui assinatura de
código. Não descreva uma build como “assinada” ou “verificada pelo Windows”.

## Verificar um download

Baixe o executável e `SHA256SUMS.txt` da mesma release em
`https://github.com/SamTecDev7/nexo-actions/releases/tag/vX.Y.Z`.

No PowerShell:

```powershell
(Get-FileHash .\NexoSetup.exe -Algorithm SHA256).Hash.ToLowerInvariant()
Get-Content .\SHA256SUMS.txt
```

No Linux/WSL:

```bash
sha256sum -c SHA256SUMS.txt
```

No PowerShell, compare o valor exibido com a linha de `NexoSetup.exe` sem
ignorar diferenças. Se não coincidir, não execute o arquivo.

## Falha, correção e rollback

- Falha antes da publicação: corrija o código e mova/recrie a tag local somente
  se ela ainda não tiver sido enviada.
- Tag já enviada, sem release: não force uma tag pública; publique uma versão de
  correção.
- Release já publicada: preserve o histórico e lance um novo patch. Se houver
  risco de segurança, marque a release afetada claramente e siga `SECURITY.md`.
- Workflow falhou: abra a execução, identifique o primeiro job com erro e
  reproduza localmente o comando correspondente antes de reexecutar.

Nunca contorne `ci_check_repository.py`, altere os artefatos depois da validação
ou reutilize o mesmo número de versão para binários diferentes.
