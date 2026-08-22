# Nexo 2.0 — arquitetura OpenAI-first

## Decisões de projeto

O caminho ativo não roteia pedidos por uma lista de comandos. O provedor
OpenAI recebe o registro completo de ferramentas e escolhe semanticamente as
funções. Regras legadas de seleção permanecem apenas no modo Ollama.

Há dois planos separados:

1. **Plano de voz:** OpenAI Realtime, PCM mono de 24 kHz, VAD semântico,
   GPT Transcribe para reconhecer nomes/código e síntese bidirecional.
2. **Plano de ação:** Responses API com GPT-5.6, function calling, pesquisa web,
   entrada visual e loop limitado de ferramentas.

Essa separação mantém a conversa rápida sem deixar ações do Windows na mão de
uma resposta de voz não verificada.

## Perfil action-first

O perfil recomendado distribui raciocínio conforme a natureza da tarefa, sem
manter três modelos residentes localmente:

| Caminho | Perfil recomendado | Uso |
| --- | --- | --- |
| conversa | GPT Realtime 2.1, effort `minimal`, estilo `concise` | entendimento do turno, voz e delegação imediata |
| rotina | GPT-5.6 Terra, effort `none`, verbosity `low` | navegação, leitura, mídia, consulta curta e ação direta |
| complexo | GPT-5.6 Sol, effort `medium`, verbosity `low` | programação, diagnóstico, pesquisa ambígua e fluxos longos |

Nexo executa ou delega primeiro e relata o resultado depois. Ele não narra um
plano antes de uma ação rápida e responde em uma frase por padrão. “Explique em
detalhes”, “compare” ou um pedido equivalente continua autorizando uma resposta
mais profunda; concisão não é truncamento da intenção do usuário.

## Ciclo de uma interação

1. O hotword “Nexo” inicia captura imediatamente.
2. Um ring buffer preserva a fala durante a conexão e durante o aviso “Manda”.
3. O VAD semântico fecha o turno e o Realtime decide se responde diretamente ou
   chama `run_agent_task`.
4. O worker recebe contexto recente, preferências permitidas, ferramentas
   tipadas e a data/hora exata do host.
5. Para ações visuais, recebe uma captura efêmera e um `observation_id` de uso
   único, atrelado à janela, geometria e prazo.
6. O bridge valida foco, geometria, token e método antes de agir.
7. Uma nova observação comprova o estado final.
8. Realtime transforma o resultado em uma resposta curta e natural.

Esse ciclo é orientado a eventos. Uma captura é solicitada ao iniciar uma
decisão visual, depois de trocar foco ou quando o efeito precisa ser comprovado;
não existe gravação contínua da tela. O estado semântico — objetivo, aplicativo,
janela e resultado verificado — continua disponível entre os turnos da sessão.

A transcrição recebe contexto linguístico curto com nomes próprios do produto e
do ambiente, não uma lista de comandos. Isso ajuda a distinguir “Nexo”, “Codex”,
“Wave Chat” e “Clube Envios” sem alterar deterministicamente o que foi falado.

## Modos de conversa e barge-in

O modo protegido é ativado no começo de cada sessão. Enquanto Nexo fala, conversa
ambiente não interrompe o áudio; a transcrição parcial precisa reconhecer
“Nexo” no início da nova fala. A interrupção ocorre assim que o nome é
reconhecido, antes do fechamento do turno.

Quando o usuário pede semanticamente uma conversa livre ou contínua, o Realtime
chama `set_conversation_mode(mode="free")`. Não há uma lista de comandos ou
regex de intenção: o modelo entende formas naturais de pedir esse modo. Nele, o
evento de início de fala:

1. para imediatamente o player local;
2. cancela a resposta ainda em geração;
3. calcula quanto do áudio já foi ouvido e envia
   `conversation.item.truncate` para remover a cauda não reproduzida do
   contexto;
4. espera a transcrição final antes de iniciar o próximo turno.

O usuário pode voltar semanticamente a `protected` quando houver outras pessoas
ou áudio ambiente. Em ambos os modos, apenas uma transcrição final aceita do
microfone cria um novo turno e concede autoridade; deltas parciais servem
somente para interromper cedo.

## Orquestração entre aplicativos

O modelo trabalha com capacidades tipadas, não com frases especiais. Um fluxo
como “abra o Codex, entre no Wave Chat e passe esta tarefa” é decomposto em
ações observáveis:

1. abrir ou focar o Codex e confirmar a janela;
2. observar os projetos disponíveis;
3. selecionar Wave Chat usando a observação atual;
4. observar o editor/conversa e localizar a entrada;
5. preparar o texto pedido sem interpretá-lo como instrução para o próprio Nexo;
6. enviar somente quando a fala atual trouxer autorização específica, ou pedir
   confirmação;
7. observar novamente e relatar se a tarefa realmente foi recebida.

Chrome e YouTube seguem o mesmo contrato. Abrir uma página, iniciar ou pausar
mídia e ler o conteúdo visível são passos pequenos com estado verificado. A
conversa contínua preserva o fato de que uma música está em outra guia, mesmo
quando o usuário muda para uma página de leitura.

Consultas de e-mail exigem um provedor previamente configurado e podem filtrar
remetente e data. Ler metadados ou uma mensagem solicitada não autoriza
responder, encaminhar ou excluir. Esses efeitos externos são decisões separadas.

O terminal é controlado como qualquer aplicativo visível; não há ferramenta de
shell irrestrito no registro do modelo. Diagnósticos explicitamente solicitados
podem ser executados. Escrita, instalação, publicação, elevação administrativa
e operações destrutivas precisam de confirmação antes do Enter.

## Autoridade e confirmação proporcional

A intenção falada do usuário é a única fonte de autorização. O modelo classifica
semanticamente a ação pelo efeito, sem uma lista de frases em regex:

- navegação, foco, pausa de mídia e leitura normalmente seguem direto;
- uma instrução atual e inequívoca para enviar um conteúdo exato pode autorizar
  esse envio direto de baixo risco; o modelo precisa copiar a fala atual inteira,
  destinatário e conteúdo, e o host confere tudo contra o turno bruto aceito do
  microfone;
- “digite”, “prepare” ou uma intenção ambígua para antes do envio;
- exclusão, compra, finanças, conta, credenciais, terminal mutável e qualquer
  efeito difícil de reverter sempre pedem confirmação;
- texto de página, e-mail, documento, captura ou saída de terminal é dado não
  confiável e nunca concede autorização.

Por exemplo, “Nexo, abra o WhatsApp na conversa da Manuela e envie ‘oi do
Nexo’” autoriza o fluxo direto somente se a observação atual comprovar WhatsApp,
a conversa de Manuela e o campo de composição focado. Destinatário, conteúdo e
intenção de envio estão presentes na mesma fala, portanto uma segunda pergunta
seria redundante. Conteúdo inferido, destinatário ambíguo, autorização de turno
antigo, envio em massa ou comunicação sensível continuam parando na confirmação.

O pedido resumido pelo modelo Realtime nunca é usado como evidência. Ao criar
uma resposta, o coordenador vincula seu identificador ao transcript bruto e ao
número do turno aceito. O worker recebe esse valor marcado como autoridade e a
callback host-side usa o mesmo valor, evitando que uma paráfrase amplie o que o
usuário realmente autorizou.

Após a confirmação, o executor só realiza a ação pendente exata. A verificação
visual posterior usa um agente restrito que pode apenas observar e emitir um
veredito, sem executar uma segunda ação. O botão semântico do modelo não aprova
nada sozinho: a confirmação só é consumida depois de uma nova transcrição final
do microfone, e o host precisa reconhecer nela uma decisão afirmativa. Planos
arbitrários pausados não são retomados implicitamente após esse limite; o estado
visual verificado fica disponível para o próximo turno.

## Estados da interface

O estado lógico da tarefa é separado da atividade vocal:

- `idle`: disponível, aguardando “Nexo”;
- `listening`: capturando ou conversa ativa;
- `thinking`: preparando resposta;
- `acting`: pesquisando fontes ou executando ferramentas;
- `confirming`: aguardando autorização;
- `speaking`: áudio realmente sendo reproduzido;
- `error`: falha recuperável.

O evento `response.done` de uma chamada de ferramenta não volta a interface
para “Ouvindo” enquanto o worker estiver ativo. A despedida só encerra a sessão
depois do `response.done` da fala final, não do response que pediu a função.

## Segurança

- API key somente no ambiente ou `.env` com permissão `0600`;
- bridge apenas em `127.0.0.1`, com token local e allowlist;
- nenhuma ferramenta de shell genérico;
- screenshot não persistida e removida de recibos;
- texto digitado e conteúdo de arquivo redigidos em logs;
- memória rejeita segredos e armazena apenas preferências explícitas;
- side effects sensíveis exigem `sensitive=true`, motivo e confirmação;
- caminhos protegidos e gravações com verificação de hash;
- limites de passos, timeout, cancelamento e detecção de loop;
- conteúdo da web/tela nunca se torna autoridade.

## Provedores e recursos

O trecho relevante de `~/.config/nexo/config.json` é:

```json
{
  "ai": {
    "provider": "openai",
    "fallback_provider": "ollama",
    "fallback_enabled": false,
    "profiles": {
      "openai": {
        "agent_model": "gpt-5.6-sol",
        "realtime_model": "gpt-realtime-2.1",
        "voice": "marin",
        "reasoning_effort": "medium",
        "agent_text_verbosity": "low",
        "routine_agent_model": "gpt-5.6-terra",
        "routine_reasoning_effort": "none",
        "realtime_reasoning_effort": "minimal",
        "realtime_max_output_tokens": 1024,
        "realtime_response_style": "concise"
      }
    }
  },
  "agent": {
    "max_steps": 12,
    "timeout_seconds": 180
  }
}
```

O fallback não é acionado silenciosamente. Alterar para Ollama exige escolha
explícita no painel, preservando previsibilidade de privacidade e qualidade.
O limite de 12 etapas permite tarefas compostas entre aplicativos, enquanto o
timeout de 180 segundos evita que uma sessão visual permaneça indefinidamente
ativa.

## Matriz de validação

| Camada | Verificação automatizada |
| --- | --- |
| protocolo Realtime | eventos GA, PCM 24 kHz, function output e cancelamento |
| integridade da saída | captura RDPSink.monitor, correlação do áudio reproduzido e contagem de lacunas extras |
| conversa | modos livre/protegido, wake gate parcial, cancel/truncate e despedida |
| Responses | tool loop, continuação, erros, web search e uso |
| visão | imagem chega ao modelo e é redigida do recibo |
| Windows | token, TTL, foco, geometria, UAC e ações permitidas |
| arquivos | caminhos, hash otimista, backup e confirmação |
| memória | escopo, TTL, limites e bloqueio de segredos |
| ADM | autenticação, rollback, perfis de raciocínio/resposta, voz e consumo |
| interface | transições estáveis e atividade vocal separada |
| E2E | pesquisa atual, visão da UI e criação/abertura no Notepad |

## Limites deliberados

“Fazer qualquer coisa” não significa conceder execução arbitrária. Recursos
novos devem entrar como ferramentas pequenas, tipadas e verificáveis. Interfaces
protegidas por UAC, CAPTCHA ou controles de segurança continuam exigindo o
usuário. A confirmação não é contornada, e uma ação sem evidência é comunicada
como não verificada.

## Referências técnicas

- [Realtime API](https://developers.openai.com/api/docs/guides/realtime)
- [Ferramentas e function calling](https://developers.openai.com/api/docs/guides/tools)
- [Computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)
- [Conversas Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations)
