# Changelog

Todas as mudanças relevantes do Nexo são registradas neste arquivo. O projeto
segue versionamento semântico.

## [Unreleased]

### Planejado

- assinatura de código para os executáveis Windows;
- validação contínua do instalador em uma máquina Windows/WSL descartável.

## [2.0.1] - 2026-08-22

### Corrigido

- partida a frio do WSL agora aguarda o `systemd --user`, repete ativações
  transitórias e valida o estado final antes de declarar falha;
- mensagens nativas do `wsl.exe` em UTF-16LE são decodificadas corretamente,
  sem o diagnóstico aparecer truncado como apenas `N`;
- o limite de espera da interface usa um prazo real, e processos expirados não
  podem mais ser interpretados como sucesso;
- o instalador registra o usuário WSL e o launcher sempre executa os serviços
  na conta Linux correta.

### Alterado

- execução avulsa do `Nexo.exe` sem instalação agora orienta claramente a usar
  o `NexoSetup.exe` e oferece acesso direto ao instalador;
- o launcher mantém um diagnóstico persistente em
  `%LOCALAPPDATA%\\Nexo\\logs\\launcher.log` e permite abri-lo pela tela de erro.

## [2.0.0] - 2026-08-22

### Adicionado

- arquitetura OpenAI-first com Responses e conversa Realtime;
- agente com function calling, contexto de conversa e execução multi-etapas;
- visão e controle semântico da área de trabalho por bridge local do Windows;
- pesquisa web, manipulação de arquivos, e-mail, áudio, processos, rede e
  ferramentas de sistema;
- interface holográfica e painel ADM para modelos, voz e comportamento;
- interrupção de fala, proteção contra eco e recuperação automática do
  microfone;
- pipeline WebRTC AEC dedicado entre WSL e WSLg;
- launcher nativo `Nexo.exe` e instalador interativo `NexoSetup.exe`, com
  instalação/atualização, reparo sem troca de versão e desinstalação seletiva;
- CI para Python 3.11/3.12, build Windows, varredura de credenciais, validação de
  versão e publicação de artefatos ligados ao commit por SHA-256.

### Alterado

- o provedor local permanece disponível apenas como compatibilidade congelada;
  novas instalações usam OpenAI e deixam fallback local desativado;
- respostas de voz priorizam ações e brevidade, com raciocínio maior reservado
  para tarefas complexas;
- ações externas usam classificação de risco, confirmação contextual e
  verificação de resultado.

### Segurança

- bridge restrito a localhost com token aleatório por usuário;
- chave OpenAI fora da configuração pública e dos payloads de status;
- políticas de log por ferramenta com redação de conteúdo sensível;
- confirmações de uso único para operações de maior risco.

[Unreleased]: https://github.com/SamTecDev7/nexo-actions/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/SamTecDev7/nexo-actions/releases/tag/v2.0.1
[2.0.0]: https://github.com/SamTecDev7/nexo-actions/releases/tag/v2.0.0
