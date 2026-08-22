# Fase 4 — e-mail modular

## Providers

`EmailProvider` define operações independentes de provedor para pesquisar,
ler, listar/salvar anexos, enviar, responder e encaminhar.

Implementações:

- `IMAPProvider`: IMAP TLS, SMTP SSL/STARTTLS, UID estável, mailbox configurável e segredo vindo de
  variável de ambiente ou arquivo local com permissão obrigatória `0600`;
- `GmailProvider`: Gmail API oficial com OAuth e escopos `gmail.readonly` e
  `gmail.send`; dependências são carregadas apenas quando a conta é usada.

Nenhuma conta/provedor é presumido. O alias “Suntec” passa a existir somente
depois de o usuário configurá-lo.

## Tools

- `email_accounts`
- `email_search`
- `email_find_latest`
- `email_read`
- `email_list_attachments`
- `email_save_attachment`
- `email_send`
- `email_reply`
- `email_forward`

Todas usam `log_policy=metadata_only`; corpo, token e anexos não são
registrados. Busca e download usam `JobManager`, timeout e feedback de voz.
Anexos nunca sobrescrevem arquivos por padrão; overwrite exige confirmação.
As três ferramentas de envio têm risco alto e sempre exigem confirmação
imediatamente antes do efeito externo.

O `ConversationManager` mantém somente referências mínimas de conta, mensagem
e anexo, mais metadados/resumos limitados. Corpo integral não entra no resumo
persistido da conversa.

## Setup

IMAP:

```bash
~/.venvs/ovos/bin/python scripts/setup_email.py imap \
  --account Suntec --host imap.exemplo.com --username usuario@exemplo.com \
  --smtp-host smtp.exemplo.com --smtp-port 465 --smtp-ssl
```

Gmail OAuth:

```bash
pip install google-auth-oauthlib google-api-python-client
~/.venvs/ovos/bin/python scripts/setup_email.py gmail \
  --account Suntec --address usuario@gmail.com \
  --client-secrets /caminho/client_secret.json
```

O setup nunca imprime senha/token e grava secrets fora do repositório em
`~/.config/nexo/secrets/email` com permissão `0600`.

## Validação

Sem credenciais do usuário, nenhum servidor externo foi acessado. Os testes com
provider simulado validam roteamento, pesquisa, leitura, anexos, envio,
resposta, encaminhamento, MIME e proteção do arquivo secreto.
