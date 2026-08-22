# Fase 1 — conversa contínua

## Implementação

- `ConversationManager` mantém uma sessão local com TTL padrão de 180 s,
  no máximo 24 mensagens e orçamento aproximado de 2.200 tokens.
- Histórico antigo é compactado; resultados de tools são reduzidos e campos
  sensíveis são removidos.
- Referências como último aplicativo, processo, caminho e tool ficam disponíveis
  para pedidos como “fecha ele” e “abre de novo”.
- “Vamos conversar” é capturado pelo fallback de alta prioridade do Nexo para
  não perder a frase para a Persona genérica instalada no sistema.
- A skill combina `FallbackSkill` e `ConversationalSkill`. A ativação usa os
  eventos nativos `intent.service.skills.activate` e o pipeline
  `ovos-converse-pipeline-plugin`.
- Durante a sessão, o listener muda para `continuous`; ao sair ou expirar,
  restaura o modo anterior (`wakeword`, `hybrid` ou `continuous`).
- O timeout usa o agendador nativo de skills do OVOS, sem criar outro loop de
  microfone.

## Barge-in e proteção de eco

A versão instalada do `ovos-dinkum-listener` (0.5.0) publica o fim da gravação
antes de entregar a transcrição. Esse limite agora é tratado apenas como um
candidato a interrupção: o Nexo não encerra mais o TTS cegamente nesse evento.

Quando o STT chega, `EchoGuard` compara o texto com as falas recentes usando
fragmentos, janelas de palavras, alinhamento tolerante a erros do ASR, novidade
de vocabulário e marcadores explícitos como “Nexo, para um pouco”. Eco puro é
descartado antes de entrar no histórico ou numa confirmação. Uma fala humana
confirmada emite `mycroft.audio.speech.stop` apenas para o áudio e segue pelo
turno normal.

O listener mantém `mute_during_output=false`, portanto o barge-in humano
continua possível. `fake_barge_in=true` reduz temporariamente a caixa para 20%
quando a gravação começa, diminuindo a mistura acústica sem silenciar a resposta.
O instalador aplica essa configuração de forma atômica, idempotente e com backup.

## Evidência de integração

O cenário `scripts/nexo_bus.py smoke-conversation` comprovou:

1. `vamos conversar` → Nexo respondeu `Claro.`;
2. listener recebeu `mode=continuous`;
3. o turno seguinte foi escolhido por `ovos-converse-pipeline-plugin`;
4. `como está minha GPU?` consultou dados reais da RTX 5070;
5. `pode parar` desativou a sessão e restaurou `mode=wakeword`.

Os testes automatizados cobrem fragmento exato, palavra deformada pelo ASR,
seguimento humano com palavras novas, marcador de interrupção, janela expirada,
ausência de stop no limite de gravação e stop seletivo após confirmação humana.

## Configuração da skill

- `conversation_timeout_seconds` (padrão `180`)
- `conversation_max_messages` (padrão `24`)
- `conversation_max_tokens` (padrão `2200`)
- `conversation_barge_in` (padrão `true`)
- `conversation_echo_guard` (padrão `true`)
- `conversation_echo_window_seconds` (padrão `8`)
- `barge_in_volume_percent` em `~/.config/nexo/config.json` (padrão `20`)

## Limite conhecido

A separação perfeita de duas vozes simultâneas ainda depende do microfone e da
distância da caixa. A defesa em duas camadas evita o ciclo de autorresposta mesmo
quando o dispositivo Windows/WSL não oferece cancelamento de eco de hardware.
