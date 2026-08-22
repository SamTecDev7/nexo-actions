# Fase 3 — agente multi-tool

## Orquestrador

`AgentOrchestrator` implementa o ciclo:

`Gemma → tool tipada → resultado compacto → Gemma → próxima tool/resposta`

Garantias:

- máximo configurável de 8 passos (limite absoluto 16);
- timeout global padrão de 90 segundos;
- cancelamento cooperativo via `JobManager`;
- deduplicação por nome + argumentos;
- detecção de loop com síntese final sem tools;
- resultados limitados e secrets redigidos;
- pausa antes de uma tool que exige confirmação;
- resposta final curta e adequada para voz.

Pedidos compostos rodam fora do fallback síncrono. O Nexo responde de imediato e
fala no máximo um aviso de progresso depois de quatro segundos.

## Diagnóstico garantido

Para pedidos de lentidão/diagnóstico, uma resposta antecipada do modelo não é
aceita enquanto faltar:

1. `system_status(scope="all")`;
2. `list_processes(sort_by="cpu", limit=5)`;
3. `list_processes(sort_by="memory", limit=5)`.

Isso foi necessário porque, no primeiro ensaio, o Gemma tentou concluir depois
das duas primeiras coletas. Após a garantia estrutural, o ensaio real executou
as três em 15,9 segundos e atribuiu a maior pressão a `vmmem` (~8 GB e ~22% de
CPU naquele instante).

## ToolSelector

Antes de cada chamada simples ou agente, o catálogo é filtrado por domínio:
áudio, apps, navegador, desktop, arquivos, armazenamento, sistema, processos,
rede, IA, e-mail e energia. Por exemplo, um pedido de volume recebe apenas as
quatro tools de áudio; um diagnóstico recebe sistema/processos/rede/IA.

## Literais do usuário

Em criação de notas, filename, diretório Downloads e conteúdo entre aspas são
preservados deterministicamente. Isso corrige trocas ocasionais de caracteres
como `o`/`0` feitas por modelos locais pequenos.

## Runtime do próprio Nexo

`ai_runtime_status` consulta `/api/ps` do Ollama e a GPU. No ensaio real:

- Ollama respondeu;
- `gemma4:12b` / 11.9B / Q4_K_M estava carregado;
- 7.829.507.275 bytes estavam alocados em VRAM pelo modelo;
- contexto reportado: 4096 tokens.

## Workflow Bloco de Notas

O smoke test agente recebeu o pedido completo, escolheu apenas
`create_text_file`, preservou `reunião amanhã às nove`, salvou em Downloads,
abriu no Bloco de Notas, validou conteúdo/janela e removeu o arquivo de teste.
