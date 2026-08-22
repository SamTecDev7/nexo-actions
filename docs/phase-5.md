# Fase 5 — ações sensíveis e interface holográfica

## Segurança operacional

Foram adicionadas exclusão para Lixeira/permanente e ações de bloqueio,
suspensão, reinício e desligamento. Exclusão e energia são ferramentas de risco
alto com confirmação one-shot. Exclusão permanente usa nível `strong` e ignora
aprovações genéricas; somente uma frase inequívoca é aceita.

O bridge também aplica defesa em profundidade: itens enviados à Lixeira devem
estar dentro do perfil Windows e raízes como Desktop, Downloads e Documentos não
podem ser removidas. O servidor continua em `127.0.0.1` com token privado.

## Holograma

A interface é servida localmente, sem CDN ou telemetria. O Canvas desenha uma
esfera de centenas de partículas em projeção 3D, deformação orgânica, pulsação,
paralaxe, retículas e anéis orbitais. A energia, velocidade e cor mudam nos
estados:

- `idle`: respiração ciano suave;
- `listening`: expansão sensível à voz;
- `thinking`: rotação violeta acelerada;
- `speaking`: pulsos ciano sincronizados à fala;
- `acting`: verde e progresso da ferramenta;
- `confirming`: âmbar e aviso explícito;
- `error/offline`: vermelho controlado.

SSE mantém a tela sincronizada com o messagebus. O campo de texto emite uma
utterance no mesmo pipeline local e exige cabeçalho/content-type que bloqueiam
POST cross-origin simples. CSP, `X-Frame-Options: DENY`, `no-store` e bind de
loopback reduzem a superfície de ataque.

## Validação

A interface foi inspecionada em navegador real nos estados `idle`, `speaking` e
`confirming`. A suíte automatizada valida servidor estático, cabeçalhos,
restrição de bind, estado versionado, limites de texto, entrada local e todas as
ferramentas existentes.
