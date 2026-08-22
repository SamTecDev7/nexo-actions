# Fase 2 — desktop, arquivos e navegador

## Windows Bridge

O companion persistente roda no Windows com Python 3.11 e somente biblioteca
padrão. Ele:

- escuta exclusivamente em `127.0.0.1:8765`;
- exige token aleatório de 256 bits em toda chamada;
- limita o corpo a 64 KiB;
- expõe apenas métodos allowlisted, sem shell/PowerShell arbitrário;
- registra somente nome do método e chaves dos parâmetros;
- restaura texto anterior da área de transferência após `type_text` quando
  havia texto disponível.

O WSL desta máquina usa NAT e não encaminha o loopback do Windows. Para não
abrir uma regra de firewall, `proxy.py` recebe o JSON por `stdin` e chama o
bridge pelo loopback do Windows. Token e conteúdo não aparecem nos argumentos
do processo.

`scripts/setup_windows.ps1` copia o bridge para
`%LOCALAPPDATA%\Nexo\bridge`, cria o token com ACL restrita, registra a tarefa
de login `Nexo Windows Bridge`, reinicia somente uma instância conhecida pelo
PID file e valida o health check. O script é idempotente e compatível com
Windows PowerShell 5.1.

## Tools adicionadas

### Navegador

- `open_url`
- `open_website`
- `web_search`

URLs aceitam apenas HTTP(S), não permitem credenciais embutidas e validam o
host. Sites conhecidos vêm de `site_aliases`; nomes desconhecidos viram busca
por “site oficial”, sem domínio inventado.

### Desktop

- `bridge_health`
- `list_windows`
- `active_window`
- `focus_window`
- `window_action`
- `show_desktop`
- `type_text`

Foco e ações usam Win32 por handle; digitação usa foco + paste Unicode e não
coordenadas de mouse.

### Arquivos

- `open_folder`, `open_file`, `reveal_file`
- `find_files`
- `create_folder`, `create_text_file`
- `copy_file`, `move_file`, `rename_file`

Nomes reservados do Windows e caracteres inválidos são rejeitados. Arquivos
existentes não são sobrescritos por padrão. Busca tem limite, timeout,
cancelamento cooperativo e progresso pelo `JobManager`.

## Confirmações

`ConfirmationManager` mantém uma ação pendente por até 60 segundos e entende
respostas afirmativas/negativas na conversa nativa do OVOS. Fechar janela,
force-kill, mover/renomear e sobrescrever exigem confirmação conforme contexto
e metadados do registro.

## Validação real

`scripts/smoke_phase2.py --open-site` comprovou no Windows:

- bridge saudável (PID 15548 no ensaio);
- criação de arquivo temporário em Downloads;
- abertura no Bloco de Notas;
- enumeração da janela por título;
- minimizar, restaurar e fechar pelo bridge;
- limpeza do arquivo temporário;
- alias `club envios` abrindo `https://clubenvios.com.br`.

A suíte chegou a 42 testes depois da inclusão dos fast paths e riscos
condicionais.
