# Nexo 2.0

[![CI](https://github.com/SamTecDev7/nexo-actions/actions/workflows/ci.yml/badge.svg)](https://github.com/SamTecDev7/nexo-actions/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/SamTecDev7/nexo-actions?display_name=tag)](https://github.com/SamTecDev7/nexo-actions/releases/latest)

Nexo é um assistente pessoal de voz para Windows + WSL. A versão 2.0 usa a
OpenAI como cérebro principal para conversa em tempo real, visão da tela,
pesquisa atual e planejamento de ações. O caminho local legado permanece
congelado para compatibilidade: instalações novas não baixam Ollama nem modelos
LLM locais, e o fallback local fica desativado.

O objetivo não é manter uma lista rígida de frases. O modelo recebe ferramentas
tipadas, escolhe semanticamente o que usar, observa o resultado e só então
responde. A interface permanece local: uma tela preta com um holograma vivo que
reage separadamente a escuta, pensamento, execução, confirmação e fala.

## Capacidades principais

- conversa natural em português brasileiro com OpenAI Realtime e transcrição
  de alta precisão por GPT Transcribe;
- ativação por “Nexo” e continuidade sem repetir o nome a cada turno;
- encerramento por “tchau”, “até mais”, “pode encerrar” e equivalentes;
- dois modos semânticos de conversa: protegido, em que somente uma fala iniciada
  por “Nexo” interrompe; e livre, em que qualquer nova fala interrompe
  naturalmente após o usuário pedir uma conversa contínua;
- buffer do começo da frase para não perder palavras ditas logo após “Manda”;
- pesquisa web nativa e atualizada, com validação de entidade, cidade e contexto;
- visão orientada a eventos e fluxo observar → agir → verificar;
- controle de janelas, aplicativos, navegador, mídia, teclado, mouse e volume;
- continuidade de contexto entre aplicativos, janelas e turnos da conversa;
- leitura, criação e edição real de arquivos, inclusive HTML, CSS e JavaScript;
- consulta de e-mail quando uma conta tiver sido configurada;
- memória local apenas para preferências explícitas e recibos sem conteúdo;
- painel ADM para provedor, modelos, voz, STT, TTS, consumo e saúde dos serviços;
- confirmações proporcionais ao risco e auditoria somente de metadados.

## Arquitetura

```text
Microfone / NexoAECSink
          │
          ▼
OpenAI Realtime ── conversa, VAD semântico e áudio 24 kHz
          │
          └── run_agent_task
                    │
                    ▼
          OpenAI Responses / GPT-5.6
                    │
        ┌───────────┼────────────┐
        ▼           ▼            ▼
  web_search   ToolRegistry   visão da tela
                    │            │
                    └──── Windows Bridge autenticado
                                  │
                         observar → agir → verificar
```

Realtime cuida da naturalidade vocal. Tarefas, fatos atuais e ações são
delegados ao worker de Responses. O bridge do Windows aceita apenas métodos
permitidos, em loopback e com token local. Nenhum shell genérico é oferecido ao
modelo.

O perfil recomendado é orientado à ação e à latência:

- o Realtime usa raciocínio `minimal`, resposta `concise` e delega antes de
  explicar; por padrão, Nexo informa apenas o resultado em uma frase;
- navegação, leitura, mídia e consultas simples usam GPT-5.6 Terra com effort
  `none`;
- programação, diagnóstico, pesquisa ambígua e fluxos complexos preservam
  GPT-5.6 Sol com effort `medium`;
- um pedido explícito por detalhes, comparação ou passo a passo continua
  recebendo a profundidade solicitada.

A visão é orientada a eventos: Nexo captura o estado quando precisa decidir,
quando muda de janela e depois de uma ação. Ele não grava continuamente a área
de trabalho. Cada observação expira e só vale para a janela e geometria em que
foi criada.

## Instalação

A instalação recomendada é feita pelo `NexoSetup.exe`, sem parâmetros. Baixe o
instalador e `SHA256SUMS.txt` na
[release mais recente](https://github.com/SamTecDev7/nexo-actions/releases/latest),
confira o hash e abra o executável. O assistente pergunta o necessário e cuida
de WSL, Ubuntu, Python, OVOS, OpenAI, áudio/AEC, serviços, bridge, atalhos e
diagnóstico.

> **Primeira instalação:** baixe e execute `NexoSetup.exe`. O arquivo
> `Nexo.exe` é o inicializador usado **depois** da instalação e não deve ser
> aberto isoladamente na pasta Downloads. Se isso acontecer, ele agora oferece
> o botão **Instalar / Reparar** e encaminha ao instalador correto.

Pré-requisitos mínimos:

- Windows 11 x64 com virtualização habilitada;
- acesso de administrador durante a instalação;
- internet e uma `OPENAI_API_KEY` ativa;
- microfone e saída de áudio reconhecidos pelo Windows.

Não é necessário instalar previamente WSL, Ubuntu, Git, Python ou OVOS. Se o
Windows precisar reiniciar após habilitar WSL, execute o mesmo instalador de
novo; o fluxo detecta o estado e continua com segurança.

O wizard oferece Instalar/Atualizar, Reparar e Remover componentes ativos. Atualizações
são vinculadas ao commit exato da release e preservam chave, voz, modelos, STT,
preferências ADM, memória e dados locais. O update usa uma transação com
rollback de checkout, venv e configurações se a validação falhar. A chave passa somente por entrada
oculta/stdin e fica em `.env` com permissão `0600`; ela não entra em argumentos,
painel, logs ou no Git.

Ao final, `Nexo.exe` fica em `%LOCALAPPDATA%\Nexo\Nexo.exe`, com atalhos na área
de trabalho e no menu Iniciar. O manual completo, incluindo reboot, reparo,
desinstalação, logs e áudio, está em
[`docs/installation-windows.md`](docs/installation-windows.md).

## Uso

Exemplos naturais:

- “Nexo, abre o Chrome.”
- “Pesquise quem é a empresa Clube Envios em Goiânia.”
- “Leia essa página para mim.”
- “Pause o vídeo.”
- “Crie em Downloads um formulário moderno com HTML, CSS e JavaScript.”
- “Abra o arquivo que criamos e deixe o fundo mais claro.”
- “Lembre que minha música preferida é Estrelinha.”
- “Abra minha música preferida no YouTube.”
- “Abra o Codex, entre no projeto Wave Chat e passe esta tarefa: ...”
- “Veja se chegou hoje algum e-mail da OpenAI.”
- “Abra o terminal e mostre o status do serviço do Nexo.”
- “Até mais.”

Depois da ativação, fale normalmente. Durante uma resposta, use “Nexo, pare” ou
“Nexo, mas o que isso significa?” para interromper. Sem o nome, falas paralelas
não iniciam outro fluxo no modo protegido. Para uma conversa com interrupção
natural, peça com suas próprias palavras — por exemplo, “Nexo, vamos conversar
livremente”. Nexo muda semanticamente para o modo livre, sem lista de frases
especiais. Peça para voltar ao modo protegido quando estiver em público.

No modo livre, o início da nova fala para o player imediatamente, cancela a
resposta em geração e remove do contexto o trecho de áudio que ainda não foi
ouvido. No modo protegido, a transcrição parcial reconhece “Nexo” no começo e
faz a mesma interrupção antes de esperar a frase inteira.

## Fluxos entre aplicativos

Nexo mantém o objetivo e o estado visível durante uma tarefa composta. Em vez
de transformar a fala numa sequência fixa de atalhos, o modelo decide o próximo
passo, observa a janela certa, executa uma ação pequena e comprova o efeito.

No exemplo do Codex, o fluxo é: abrir ou focar o aplicativo, observar a lista de
projetos, abrir **Wave Chat**, observar o editor, preparar a tarefa, decidir se
a autorização atual permite o envio e verificar que ela entrou na conversa.
“Digite esta tarefa” significa preparar; “passe/envie esta tarefa” pode
autorizar o envio exato quando não houver outro risco. Se a intenção estiver
ambígua, Nexo pergunta antes. A autorização é ligada a um trecho literal da fala
atual; uma resposta do modelo, texto da tela ou resultado de ferramenta não pode
confirmar a ação por conta própria.

O mesmo contexto vale entre Chrome e YouTube. Ele pode iniciar uma música,
continuar conversando, pausar o player quando solicitado e depois ler o conteúdo
visível de outra página. E-mail usa a conta explicitamente configurada: consultas
podem ser feitas diretamente, enquanto enviar, responder, excluir ou alterar
conta segue a política de confirmação.

Em mensageiros, a autorização é proporcional. “Nexo, abra o WhatsApp na
conversa da Manuela e envie ‘oi do Nexo’” pode abrir a conversa, conferir
visualmente o destinatário e o campo focado e enviar sem perguntar de novo,
pois destinatário, conteúdo e intenção estão explícitos no turno atual. Se
qualquer parte for inferida, antiga, ambígua, em massa ou sensível, Nexo prepara
o que for reversível e pede confirmação antes do envio.

No terminal, Nexo interage com a janela visível. Um comando de diagnóstico
explicitamente pedido pode ser executado e lido de volta. Comandos que gravam,
instalam, publicam, usam privilégios administrativos ou podem destruir dados
exigem confirmação antes do Enter. Conteúdo exibido pelo terminal ou por uma
página nunca é tratado como autorização do usuário.

## Painel ADM

Diga “Nexo, abra as configurações ADM” ou acesse
`http://127.0.0.1:8787/admin`. No primeiro acesso, crie uma senha exclusiva do
Nexo; ela não é a senha do Windows.

O painel permite:

- alternar OpenAI e Ollama;
- escolher GPT-5.6 Sol, Terra ou Luna;
- escolher GPT Realtime 2.1 ou a opção Mini;
- escolher a voz OpenAI, incluindo Marin e Cedar;
- ajustar raciocínio do agente, caminho rápido, raciocínio Realtime, estilo de
  resposta, STT e TTS local;
- testar a credencial sem expô-la ao navegador;
- gerar o aviso “Manda” com a voz selecionada;
- reaplicar automaticamente o microfone após reconexão da placa externa;
- acompanhar requisições, tokens e latência, sem guardar conversas.

As vozes OpenAI são geradas por inteligência artificial. O painel mostra essa
informação junto à seleção de voz.

## Segurança e privacidade

A fala final aceita diretamente do microfone é a autoridade. Um resumo feito
pelo modelo de voz não substitui esse turno bruto. Texto de páginas, imagens e
resultados de busca é tratado como dado não confiável, não como instrução.
Comunicação direta de baixo risco pode seguir quando destinatário, conteúdo e
intenção de envio estiverem todos explícitos e a tela confirmar o foco. Envio
ambíguo, em massa ou sensível e ações de compra, exclusão, conta, credencial,
terminal mutável ou outro efeito difícil de reverter sempre pedem confirmação.

Capturas de tela são efêmeras, limitadas a uma observação e não são gravadas no
histórico. Transcrições, prompts, respostas, chaves e conteúdo de arquivos não
entram no ledger. Ele guarda somente provedor, modelo, quantidade de tokens,
latência e identificadores técnicos.

Uma ação visual não é considerada concluída só porque o evento de mouse ou
teclado foi aceito. O agente precisa fazer uma nova observação. Abrir um
aplicativo também exige evidência de uma janela visível.

O perfil recomendado limita cada tarefa a 12 etapas e 180 segundos. Ao atingir
um limite, Nexo informa que não concluiu em vez de inventar sucesso.

## Áudio e microfone externo

O Nexo usa um servidor PulseAudio isolado com cancelamento acústico WebRTC:
`NexoAECSink` para saída e `NexoAECSource` para captura. O monitor detecta
quando uma placa USB/RDP desaparece e reaplica origem, ganho e cadeia AEC.
No perfil OpenAI, o GPT Transcribe recebe contexto de vocabulário — nomes como
Nexo, Codex, Wave Chat e Clube Envios — sem reescrever a fala por regex.

```bash
PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl info
PULSE_SERVER=unix:/tmp/nexo-pulse/native pactl list short sources
~/.venvs/ovos/bin/python -m nexo_actions.audio_recovery check
~/.venvs/ovos/bin/python -m nexo_actions.audio_recovery repair --reason manual
```

Também é possível usar **Reaplicar microfone** no painel ou no ícone de bandeja
do executável.

## Testes e diagnóstico

```bash
# suíte unitária e de integração
~/.venvs/ovos/bin/python -m unittest discover -s tests -v

# credencial e Responses
~/.venvs/ovos/bin/python scripts/check_openai_connection.py
~/.venvs/ovos/bin/python scripts/smoke_openai_agent.py

# Realtime sem reproduzir áudio
~/.venvs/ovos/bin/python scripts/smoke_realtime_connection.py
~/.venvs/ovos/bin/python scripts/smoke_realtime_roundtrip.py
~/.venvs/ovos/bin/python scripts/smoke_realtime_transcription.py
~/.venvs/ovos/bin/python scripts/smoke_realtime_speaker.py
~/.venvs/ovos/bin/python scripts/smoke_realtime_output_integrity.py

# visão, bridge e ação real controlada
~/.venvs/ovos/bin/python scripts/smoke_openai_vision.py
~/.venvs/ovos/bin/python scripts/smoke_bridge.py
~/.venvs/ovos/bin/python scripts/smoke_notepad_agent.py

# serviços e logs
systemctl --user status ovos-core.service ovos-listener.service nexo-ui.service
journalctl --user -u ovos-core.service -n 100 --no-pager
```

Mais detalhes técnicos e a matriz de validação estão em
[`docs/openai-jarvis.md`](docs/openai-jarvis.md).
