# Nexo — Fase 0

## Diagnóstico inicial

- O pacote `nexo-actions` instalado aponta diretamente para este workspace.
- Ambiente observado: OVOS Core 2.1.1, `ovos-dinkum-listener` 0.5.0 e `ovos-audio` 1.2.0.
- O scanner legado executava uma recursão PowerShell completa para cada pasta de primeiro nível.
- Baseline real em 9 de agosto de 2026:
  - Downloads: 2,499 s (cache de arquivos do sistema aquecido).
  - Perfil do Windows: timeout após 90,094 s, sem progresso e sem resultado.

## Correção de armazenamento

Foram comparados dois backends com a mesma árvore de Downloads:

- `os.scandir()` via `/mnt/c`: 8,465 s de ponta a ponta; 6,488 s de enumeração.
- travessia única nativa do Windows: 2,743 s de ponta a ponta; 0,522 s de enumeração.

O backend nativo foi escolhido para o ambiente real. O backend Python permanece como fallback e é usado nos testes com diretórios temporários.

No perfil completo, o novo scanner respeitou 20 s, emitiu progresso e retornou resultado parcial após examinar 99.349 arquivos e 11.492 pastas. Resultados completos entram em cache temporário; parciais não são cacheados.

Na execução final do benchmark reproduzível, Downloads terminou em 2,712 s. O perfil retornou em 20,005 s, com quatro eventos de progresso e um parcial baseado em 121.152 arquivos e 13.633 pastas.

## Dívida técnica inventariada

- `skill.py` ainda concentra roteamento, prompt, feedback de voz e execução; será dividido nas fases de conversação e agente.
- Os schemas legados permanecem próximos aos handlers, embora registro, metadados e dispatch agora sejam centrais.
- Ainda há chamadas PowerShell repetidas nas áreas de áudio, processos, rede e sistema; o Windows Bridge substituirá esse custo gradualmente.
- Configuração do modelo, timeouts, aliases e segurança ainda não está centralizada em `~/.config/nexo`.
- Os arquivos `.bak` documentam a evolução incremental, mas não substituem versionamento; este workspace não possui repositório Git.
- Cancelamento cooperativo existe no núcleo de jobs, mas a frase de voz para cancelar será conectada ao modo conversacional.
- Conversa contínua, contexto, barge-in e UI holográfica pertencem às próximas fases e ainda não fazem parte da Fase 0.

## Infraestrutura

- `ToolRegistry`: schemas Ollama gerados a partir de um registro único, validação de argumentos, categorias, risco, confirmação, timeout, long-running e política de log/redação.
- `JobManager`: pool controlado, IDs, estados, timeout, cancelamento cooperativo, progresso, callbacks, deduplicação e limpeza.
- A skill não cria mais uma thread avulsa para cada operação lenta.

## Como validar

```bash
cd ~/nexo-actions
~/.venvs/ovos/bin/python -m unittest discover -s tests -v
~/.venvs/ovos/bin/python scripts/benchmark_storage.py --scope all --backend auto --timeout 20
```
