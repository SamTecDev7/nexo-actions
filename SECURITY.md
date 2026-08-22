# Segurança do Nexo

O Nexo é um assistente com acesso ao microfone, à tela, a arquivos e a ações no
Windows. Ele deve ser tratado como um aplicativo local com os mesmos privilégios
da conta que o executa, não como uma fronteira de segurança ou um sandbox.

## Versões com suporte

Enquanto o projeto estiver na série 2.0, correções de segurança são mantidas na
versão 2.0.x mais recente. Versões anteriores, cópias modificadas e builds de
terceiros não são cobertos por esta política.

## Como reportar uma vulnerabilidade

Não publique chaves, transcrições, capturas de tela, tokens ou dados pessoais em
uma issue pública.

Após a publicação do repositório, use preferencialmente **Security > Report a
vulnerability** no GitHub. Se essa opção ainda não estiver habilitada, contate o
mantenedor por um canal privado antes de abrir uma issue. Inclua:

- versão ou commit afetado;
- impacto e pré-condições;
- passos mínimos para reproduzir;
- logs já sanitizados;
- sugestão de correção, se houver.

Espere uma confirmação de recebimento antes de divulgar detalhes. O prazo de
triagem e de correção depende da severidade e da reprodutibilidade; não há, por
enquanto, um SLA formal ou programa de recompensa.

## Modelo de ameaça e limites de confiança

O desenho atual considera confiáveis o usuário interativo do Windows, sua conta
WSL e a máquina local. O Nexo não protege contra malware, outro processo com os
mesmos privilégios do usuário, uma conta Windows comprometida, extensão de
navegador maliciosa ou alguém com acesso físico à sessão desbloqueada.

Os principais limites são:

- o modelo OpenAI recebe o contexto necessário à conversa ou ação. Conforme a
  tarefa, isso pode incluir áudio, transcrição, texto, resultados de ferramentas
  e observações da tela;
- o bridge executa operações no Windows com os privilégios do usuário conectado;
- os serviços OVOS/Nexo no WSL operam na conta Linux do usuário;
- a interface administrativa e o bridge são serviços locais, não serviços para
  exposição na rede;
- ferramentas reduzem risco com schemas, validação, escopo, confirmação e
  verificação visual, mas uma instrução ambígua ou conteúdo malicioso na tela
  ainda pode induzir uma ação incorreta.

Não use o Nexo como único controle para operações financeiras, credenciais,
produção, saúde, segurança física ou dados cuja perda seja irreversível.

## Chaves e outros segredos

`OPENAI_API_KEY` deve existir somente no ambiente do processo ou no arquivo
local `.env`. O carregador dá preferência à variável de ambiente e nunca precisa
copiar a chave para `~/.config/nexo/config.json`.

- Não faça commit de `.env`; somente `.env.example` pode ser versionado.
- No WSL, mantenha `.env` com modo `0600`.
- Não passe a chave em linha de comando, URL, issue, captura de tela ou log.
- Não reutilize a chave em outras máquinas. Revogue e substitua imediatamente
  uma chave exposta.
- A tela ADM deve exibir apenas o estado da credencial, nunca seu valor.
- Logs para suporte devem ser revisados e sanitizados antes do compartilhamento.

A CI rejeita nomes de arquivos sensíveis rastreados e padrões prováveis de
credenciais, mas essa verificação é defesa adicional, não um gerenciador de
segredos.

## Bridge do Windows

Por padrão, o bridge escuta em `127.0.0.1:8765` e aceita apenas `POST /rpc` com
um token Bearer aleatório de pelo menos 32 caracteres. O instalador restringe o
arquivo `%LOCALAPPDATA%\Nexo\bridge-token` ao usuário atual. A comparação do
token é feita de forma resistente a diferenças de tempo e o corpo da requisição
é limitado a 64 KiB.

Não altere o endereço para `0.0.0.0`, IP da LAN ou interface pública. O bridge
não implementa TLS, autenticação multiusuário nem proteção para exposição de
rede. Se o token for lido por outro processo da mesma conta, esse processo pode
invocar as operações expostas pelo bridge; encerre o bridge, remova o token e
reexecute o setup para gerar uma nova instalação local.

A UI também deve permanecer em `127.0.0.1:8787`. Regras de firewall e port
forwarding não devem publicar nenhuma dessas portas.

## Ações e confirmações

As ferramentas são classificadas como somente leitura, risco baixo, médio ou
alto. A implementação atual sempre confirma ações marcadas como alto risco ou
que exigem confirmação, incluindo exclusões, envio de e-mail e mudanças de
energia. Exclusão permanente exige uma frase de confirmação forte.

Interações visuais recebem regras adicionais:

- terminal com alteração, exclusão, finanças, conta e credenciais sempre exige
  confirmação;
- pressionar Enter ou submeter conteúdo é conservador por padrão;
- uma comunicação direta pode dispensar uma pergunta redundante somente quando
  destinatário e conteúdo aparecem explicitamente na fala atual;
- uma consulta de terminal pode dispensar confirmação somente com autorização
  explícita e verificável na fala atual;
- fechar janelas, sobrescrever certos arquivos e ações sensíveis continuam com
  proteções específicas.

Algumas escritas de baixo risco, como criar ou editar um arquivo solicitado,
podem ocorrer sem nova confirmação. Ative `safety.confirm_medium_risk` no arquivo
de configuração se desejar uma postura mais conservadora para toda ação média.
Uma confirmação expira e vale somente para a ação pendente; ela não concede
autorização permanente.

## Logs e dados locais

Ferramentas sensíveis usam políticas de log por metadados, campos redigidos ou
sem log de conteúdo. Ainda assim, caminhos de arquivo, nomes de ferramentas,
destinatários, mensagens de erro e títulos de janela podem ser dados pessoais.
Proteja os logs com as permissões da conta e retenha apenas o necessário.

Antes de anexar um log a um relatório, procure e remova chaves, tokens, e-mails,
telefones, conteúdo de mensagens, caminhos com nomes pessoais e informações da
tela. Consulte [a documentação de instalação](docs/installation-windows.md)
para os locais usados pelo instalador e pelos serviços.

## Artefatos de release

Os executáveis de release são validados como PE. O manifesto associa seus
hashes e tamanhos ao commit-fonte, e a release publica também
`SHA256SUMS.txt`. Verifique o hash antes de executar um download.

Os binários **não são assinados digitalmente** nesta versão. Um checksum obtido
do mesmo canal do arquivo detecta corrupção, mas não substitui assinatura de
código nem prova sozinho a identidade do publicador. O Windows pode mostrar um
aviso do SmartScreen; confirme a origem e o hash, ou não execute o arquivo.
