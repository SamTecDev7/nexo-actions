# Instalação no Windows com WSL

Esta é a instalação recomendada do Nexo 2.0. O fluxo é OpenAI-first: instala o
runtime de voz e ações, mas não baixa Ollama nem um LLM local. As releases
oficiais ficam em [SamTecDev7/nexo-actions](https://github.com/SamTecDev7/nexo-actions).

## Pré-requisitos

- Windows 11 x64 com virtualização habilitada no firmware;
- Ubuntu 22.04 ou 24.04 no WSL (o instalador cria uma versão compatível quando
  ainda não existe);
- uma conta com permissão de administrador durante a instalação;
- conexão com internet para Windows Update, Ubuntu, pacotes Python e GitHub;
- repositório Nexo acessível publicamente por HTTPS; o instalador não recebe
  token GitHub para clonar um repositório privado;
- microfone e saída de áudio reconhecidos pelo Windows;
- uma `OPENAI_API_KEY` ativa com acesso aos modelos configurados no Nexo;
- espaço livre para Ubuntu, OVOS e dependências Python.

Não é necessário instalar previamente Git, Python, OVOS ou Ubuntu. Se WSL já
existir, o instalador reutiliza a distribuição compatível e preserva os dados do
usuário. Feche tarefas importantes antes de começar: ativar WSL pela primeira
vez pode exigir reinicialização do Windows.

## Baixar e verificar

Na página de [releases](https://github.com/SamTecDev7/nexo-actions/releases/latest), baixe:

- `NexoSetup.exe`;
- `SHA256SUMS.txt`.

Os binários 2.0 ainda não têm assinatura de código. Antes de executar, confira o
SHA-256:

```powershell
(Get-FileHash .\NexoSetup.exe -Algorithm SHA256).Hash.ToLowerInvariant()
Get-Content .\SHA256SUMS.txt
```

O primeiro valor deve ser idêntico ao hash da linha `NexoSetup.exe`. Se houver
qualquer diferença, descarte o download. Um aviso do SmartScreen é esperado em
um binário sem assinatura; prossiga somente depois de confirmar a origem e o
hash.

## Instalação sem parâmetros

Execute `NexoSetup.exe` com duplo clique. O manifesto pede elevação pelo UAC e o
assistente faz as perguntas necessárias no próprio fluxo. Não abra um terminal
nem passe a chave como argumento.

O wizard permite confirmar a distribuição, o usuário WSL, a pasta de instalação,
a URL do repositório quando ela não estiver incorporada à build, a chave OpenAI,
atalhos, inicialização automática e validação online. Nas releases oficiais,
mantenha a URL incorporada; informe uma URL diferente apenas para um fork que
você controla.

Ao abrir, o menu oferece:

1. **Instalar ou atualizar**, usando a referência Git incorporada ao instalador;
2. **Reparar sem mudar a versão**, mantendo exatamente o checkout atual;
3. **Remover componentes ativos**, preservando projeto, venv e dados locais.

Use o Ubuntu padrão nas instalações suportadas. Os comandos abaixo assumem os
padrões `Ubuntu`, `~/nexo-actions` e `~/.venvs/ovos`; adapte somente os caminhos
se você escolheu outros valores no wizard.

Em uma instalação nova, o processo:

1. verifica ou habilita WSL 2 e os componentes necessários do Windows;
2. instala ou prepara Ubuntu e habilita systemd;
3. cria ou reutiliza o usuário Linux selecionado;
4. instala as dependências do sistema, clona o commit da release e cria
   `~/.venvs/ovos`;
5. recebe a chave OpenAI por entrada oculta e grava `~/nexo-actions/.env` com
   permissão `0600`;
6. aplica o perfil OpenAI recomendado com fallback local desativado;
7. configura OVOS, wake word, interface, cancelamento de eco e recuperação de
   microfone como serviços de usuário;
8. localiza o Python do Windows ou instala Python 3.12 pelo `winget`;
9. instala o bridge em `%LOCALAPPDATA%\Nexo`, gera um token local e cria a tarefa
   de inicialização;
10. compila/instala `Nexo.exe`, cria atalhos e executa verificações de saúde.

O bridge e a UI ficam somente em loopback (`127.0.0.1:8765` e
`127.0.0.1:8787`). Não abra essas portas no firewall. Ao concluir, abra o atalho
**Nexo** na área de trabalho ou no menu Iniciar.

### Usuário e senha do Ubuntu

Se a distribuição ainda não tiver usuário normal, o assistente solicita um nome
Linux e uma senha. Essa senha é independente da chave OpenAI e da senha do
Windows. Ela é enviada ao bootstrap pelo fluxo de entrada, não por argumento de
processo. Use ao menos oito caracteres. O usuário entra nos grupos `sudo` e
`audio` e se torna o usuário padrão da distribuição.

Em uma distribuição existente, selecione o usuário que já possui seus arquivos
e não crie uma segunda instalação no perfil `root`.

### Chave OpenAI

A chave é aceita por entrada oculta e encaminhada ao WSL por stdin. Ela não deve
aparecer em argumentos, saída ou log. No destino, fica somente em
`~/nexo-actions/.env`, com leitura e escrita do proprietário.

Por padrão, o instalador testa a credencial antes de declarar sucesso. Se o teste
indicar autenticação, quota, faturamento ou acesso ao modelo, corrija a
conta/chave e execute o instalador novamente. Nunca cole a chave em issue ou
log. Para trocá-la, reexecute o wizard e forneça a substituta; revogue a antiga
no painel da OpenAI. Em uma atualização, deixe a entrada vazia para preservar o
`.env` existente.

Quando uma chave é substituída, o instalador mescla somente
`OPENAI_API_KEY`: comentários e as demais variáveis do `.env` são preservados.
Antes da troca, uma cópia com permissão `0600` é guardada em
`~/.local/state/nexo/backups`; uma falha restaura atomicamente a versão anterior.
Um `.env` ou caminho de instalação que atravesse link simbólico é recusado.

## Reinicialização e continuação

Há dois cenários diferentes:

- **WSL já funcional:** o instalador pode encerrar e reabrir a distribuição para
  aplicar `/etc/wsl.conf`; isso não reinicia todo o Windows.
- **recurso WSL recém-ativado:** o Windows pode exigir reboot. O instalador para
  em um ponto seguro, sem registrar uma instalação concluída. Reinicie e execute
  o mesmo `NexoSetup.exe` novamente, sem parâmetros. O fluxo é idempotente e
  continua a partir do estado detectado.

Não force o encerramento enquanto pacotes estão sendo instalados. Se houver uma
queda de energia ou fechamento inesperado, reinicie o Windows quando solicitado
e reexecute o instalador; o checkout existente e a configuração são preservados
sempre que possível.

## Atualizar

Execute a versão mais nova de `NexoSetup.exe` e escolha **Instalar ou
atualizar**. Uma release oficial incorpora o SHA completo do commit que passou
pela CI. O instalador busca essa referência e deixa o checkout destacado
exatamente nesse commit, depois reinstala dependências, reaplica o perfil e
reinicia os serviços.

A atualização é serializada por mutex no Windows e por `flock` no WSL. O
checkout, o venv e as configurações relevantes entram em uma transação: os
serviços dependentes param antes da troca, voltam em ordem e só há commit da
instalação depois das verificações de saúde. Se uma etapa falhar, esses itens e
os serviços que estavam ativos são restaurados.

O atualizador não executa `git reset` nem descarta alterações locais. Se o Git
recusar a troca de commit por haver mudanças conflitantes, guarde-as em um
commit/patch ou use um checkout limpo e execute novamente. A chave só é
substituída quando uma nova é fornecida. Memória, token do bridge e arquivos de
configuração são mantidos, embora os valores do perfil recomendado e do áudio
possam ser reaplicados pelo setup.

## Reparar sem mudar a versão

Execute o instalador novamente e escolha **Reparar sem mudar a versão** quando
houver arquivo ausente, serviço parado, instalação incompleta, chave trocada ou
bridge sem resposta. Nesse modo, o Git não é atualizado: o setup reaplica as
dependências e componentes a partir do checkout existente, depois valida OpenAI,
UI, serviços e bridge. Deixe a chave vazia para preservar a atual, ou forneça uma
nova para substituí-la.

Para uma reconexão simples de placa USB/mesa de som, não é necessário reinstalar:
o watchdog tenta recuperar a cadeia automaticamente. Use **Reaplicar
microfone** no ícone de bandeja ou no painel ADM. O reparo manual pelo WSL é:

```bash
~/.venvs/ovos/bin/python -m nexo_actions.audio_recovery repair --reason manual
```

## Desinstalar

Execute `NexoSetup.exe`, escolha **Remover componentes ativos** e digite
`DESINSTALAR` quando o resumo pedir confirmação. O processo:

- desativa os serviços Nexo/OVOS instalados no WSL;
- remove as unidades de runtime e os drop-ins de áudio gerados pelo setup;
- remove a tarefa agendada, bridge, token, launcher e atalhos do Windows;
- preserva checkout Git, `.env`, `~/.venvs/ovos`, configurações Nexo/OVOS,
  memória, logs e o recibo de instalação.

Essa preservação permite reinstalar sem perder preferências: execute o setup e
escolha **Instalar ou atualizar**. Se a máquina não continuará usando o Nexo,
revogue a chave OpenAI e só então remova a cópia exata do `.env`. Os demais
diretórios preservados podem ser arquivados ou removidos individualmente após um
backup.

O desinstalador não remove Ubuntu nem o Python do Windows, pois ambos podem estar
em uso por outros projetos. `wsl --unregister` apaga irreversivelmente toda a
distribuição e não faz parte da desinstalação do Nexo.

## Logs e diagnóstico

Locais principais:

| Componente | Local |
| --- | --- |
| wizard Windows | `%LOCALAPPDATA%\Nexo\logs\installer-*.log` |
| instalação no WSL | `~/.local/state/nexo/install.log` |
| saúde do áudio | `~/.local/state/nexo/audio-health.json` |
| bridge Windows | `%LOCALAPPDATA%\Nexo\bridge.log` |
| token do bridge | `%LOCALAPPDATA%\Nexo\bridge-token` |
| parâmetros do runtime | `%LOCALAPPDATA%\Nexo\runtime.conf` |
| recibo da instalação | `%LOCALAPPDATA%\Nexo\installation.json` |
| configuração Nexo | `~/.config/nexo/config.json` |
| configuração OVOS | `~/.config/mycroft/mycroft.conf` |
| serviços WSL | journal do systemd do usuário |

Não publique o token, `.env` ou logs sem sanitização. Para um resumo de serviços:

```bash
systemctl --user --no-pager --full status \
  ovos-messagebus.service ovos-phal.service ovos-audio.service \
  ovos-core.service ovos-listener.service nexo-pulse-aec.service \
  nexo-audio-watchdog.service nexo-ui.service
```

Para os últimos eventos:

```bash
journalctl --user -u ovos-core.service -u ovos-listener.service \
  -u nexo-pulse-aec.service -u nexo-audio-watchdog.service \
  -u nexo-ui.service -n 200 --no-pager
```

Teste a credencial sem imprimir seu valor:

```bash
cd ~/nexo-actions
~/.venvs/ovos/bin/python scripts/check_openai_connection.py --env-file .env
```

## Áudio, AEC e microfone externo

O caminho normal é `RDPSource`/WSLg → `NexoAECSource` → listener, com a fala do
Nexo saindo por `NexoAECSink`. O AEC reduz a chance de o assistente ouvir a
própria voz. O watchdog verifica a cadeia sem abrir gravadores curtos a cada
ciclo, pois isso pode ressincronizar o WebRTC e cortar o começo de uma fala.

Cheque os dispositivos do servidor isolado:

```bash
PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl info
PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl list short sources
PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl list short sinks
~/.venvs/ovos/bin/python -m nexo_actions.audio_recovery check
```

O resultado esperado inclui `NexoAECSource` e `NexoAECSink`. Para um teste de
cinco segundos usando a mesma entrada do Nexo, fale normalmente durante a
gravação:

```bash
systemctl --user stop ovos-listener.service
PULSE_SERVER=unix:/tmp/nexo-pulse/native timeout 5 \
  parec --device=NexoAECSource --raw \
  --rate=16000 --channels=1 --format=s16le > /tmp/nexo-mic-test.raw
wc -c /tmp/nexo-mic-test.raw
PULSE_SERVER=unix:/tmp/nexo-pulse/native paplay --device=NexoAECSink --raw \
  --rate=16000 --channels=1 --format=s16le /tmp/nexo-mic-test.raw
systemctl --user start ovos-listener.service
```

Parar temporariamente o listener evita criar um segundo gravador sobre o fluxo
persistente e também impede que o teste reative o Nexo. `timeout` pode mostrar
`Terminated`; isso é esperado. Cinco segundos em 16 kHz mono/s16le geram cerca
de 160.000 bytes. Zero ou um tamanho muito menor indica que a origem não entregou
amostras. Se o áudio do Windows funciona, mas `RDPSource` sumiu depois de
desligar/religar a placa:

```bash
~/.venvs/ovos/bin/python -m nexo_actions.audio_recovery repair \
  --force-wslg --reason reconexao-manual
```

Esse comando recicla o áudio do WSLg e interrompe momentaneamente áudio de outras
aplicações WSLg. Não o repita em loop. Depois, confirme que
`nexo-pulse-aec.service`, `ovos-audio.service` e `ovos-listener.service` estão
`active`.

Se o microfone está baixo, ajuste `audio.input_gain_percent` no painel ADM e use
Reaplicar microfone. Não selecione `RDPSink.monitor` como fonte padrão: ele é o
monitor da caixa de som e causa autoescuta.

## Recuperação rápida

| Sintoma | Ação inicial |
| --- | --- |
| `Nexo.exe` abre, mas a UI não responde | confira `nexo-ui.service` e a porta 8787 |
| interface funciona, ações no Windows não | confira `%LOCALAPPDATA%\Nexo\bridge.log` e reexecute o setup |
| “Nexo” não ativa | confira `ovos-listener.service`, AEC e `audio_recovery check` |
| ele ouve a própria voz | confirme `NexoAECSource`/`NexoAECSink`; não use o monitor do sink como entrada |
| placa externa reconectou e ficou muda | aguarde o watchdog; depois use Reaplicar microfone |
| OpenAI responde 401 | substitua a chave ao reexecutar o setup |
| OpenAI responde 429/quota | revise limites/faturamento e tente novamente |
| update recusa o checkout | preserve alterações locais e deixe o checkout limpo para trocar ao commit da release |

Quando um serviço falhar repetidamente, colete o status e o journal, remova dados
pessoais e use o canal privado indicado em [SECURITY.md](../SECURITY.md).
