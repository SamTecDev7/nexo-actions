"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let csrf = "";
let configured = true;
let catalog = {voices: [], stt_models: [], ai_models: [], services: [], ai: {}};
let aiSettings = {};
let baseline = "";
let toastTimer = null;

function toast(message, error = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", error);
  box.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 3600);
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.json);
  }
  if (csrf && options.method && options.method !== "GET") headers.set("X-Nexo-CSRF", csrf);
  const response = await fetch(path, {...options, headers, credentials: "same-origin"});
  const type = response.headers.get("Content-Type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.blob();
  if (!response.ok) throw new Error(data.error || `Falha HTTP ${response.status}`);
  return data;
}

function setAuthMode(isConfigured) {
  configured = isConfigured;
  $("#authTitle").textContent = isConfigured ? "Núcleo ADM" : "Criar acesso ADM";
  $("#authCopy").textContent = isConfigured
    ? "Autentique-se para alterar o cérebro e a voz do Nexo."
    : "Defina uma senha exclusiva para proteger as configurações do Nexo.";
  $("#authButton").textContent = isConfigured ? "ENTRAR NO NÚCLEO" : "CRIAR ACESSO SEGURO";
  $("#confirmField").classList.toggle("hidden", isConfigured);
  $("#password").autocomplete = isConfigured ? "current-password" : "new-password";
}

async function boot() {
  try {
    const status = await request("/api/admin/status");
    setAuthMode(status.configured);
    if (status.authenticated) {
      csrf = status.csrf || "";
      await enterApp();
    } else {
      $("#authShell").classList.remove("hidden");
      $("#password").focus();
    }
  } catch (error) {
    $("#authError").textContent = error.message;
  }
}

async function authenticate(event) {
  event.preventDefault();
  const button = $("#authButton");
  const password = $("#password").value;
  $("#authError").textContent = "";
  if (!configured && password !== $("#confirmPassword").value) {
    $("#authError").textContent = "As senhas não coincidem.";
    return;
  }
  button.disabled = true;
  button.textContent = "AUTENTICANDO...";
  try {
    const result = await request(configured ? "/api/admin/login" : "/api/admin/setup", {
      method: "POST", json: {password}
    });
    csrf = result.csrf;
    $("#password").value = "";
    $("#confirmPassword").value = "";
    await enterApp();
  } catch (error) {
    $("#authError").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = configured ? "ENTRAR NO NÚCLEO" : "CRIAR ACESSO SEGURO";
  }
}

async function enterApp() {
  $("#authShell").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await refreshAll();
}

async function refreshAll() {
  setApplyState("Carregando subsistemas", "Lendo configuração local", "working");
  try {
    const [settingsResult, catalogResult, audioResult] = await Promise.allSettled([
      request("/api/admin/settings"),
      request("/api/admin/catalog"),
      request("/api/admin/audio-health")
    ]);
    if (settingsResult.status === "rejected") throw settingsResult.reason;
    if (catalogResult.status === "rejected") throw catalogResult.reason;
    const settings = settingsResult.value;
    catalog = catalogResult.value;
    populateCatalog();
    populateSettings(settings);
    renderServices();
    if (audioResult.status === "fulfilled") renderAudioHealth(audioResult.value);
    else renderAudioHealth({healthy: false, error: audioResult.reason?.message || "Diagnóstico indisponível"});
    baseline = JSON.stringify(readSettings());
    markDirty();
    setApplyState(
      "Configuração sincronizada",
      audioResult.status === "fulfilled" ? "Nenhuma alteração pendente" : "Inteligência pronta; áudio requer atenção",
      ""
    );
  } catch (error) {
    setApplyState("Falha ao carregar", error.message, "error");
    toast(error.message, true);
    if (/sessão/i.test(error.message)) lockUI();
  }
}

function option(select, value, label) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  select.append(item);
}

function populateCatalog() {
  const voiceSelect = $("#ttsModel");
  const sttSelect = $("#sttModel");
  const providerSelect = $("#aiProvider");
  const agentSelect = $("#aiAgentModel");
  const realtimeSelect = $("#aiRealtimeModel");
  const aiVoiceSelect = $("#aiVoice");
  const reasoningSelect = $("#reasoningEffort");
  const agentTextVerbositySelect = $("#agentTextVerbosity");
  const routineReasoningSelect = $("#routineReasoningEffort");
  const realtimeReasoningSelect = $("#realtimeReasoningEffort");
  const realtimeResponseStyleSelect = $("#realtimeResponseStyle");
  const ollamaSelect = $("#ollamaModel");
  voiceSelect.replaceChildren();
  sttSelect.replaceChildren();
  [
    providerSelect,
    agentSelect,
    realtimeSelect,
    aiVoiceSelect,
    reasoningSelect,
    agentTextVerbositySelect,
    routineReasoningSelect,
    realtimeReasoningSelect,
    realtimeResponseStyleSelect,
    ollamaSelect
  ]
    .forEach((select) => select.replaceChildren());
  catalog.voices.filter((voice) => voice.installed).forEach((voice) => option(
    voiceSelect, voice.id, `${voice.label} · ${qualityLabel(voice.quality)}`
  ));
  catalog.stt_models.forEach((model) => option(
    sttSelect, model.id, `${model.label} · ${model.profile}`
  ));
  const aiCatalog = catalog.ai || {};
  (aiCatalog.providers || []).forEach((provider) => option(
    providerSelect, provider.id, `${provider.label} · ${provider.profile}`
  ));
  (aiCatalog.agent_models?.openai || []).forEach((model) => option(
    agentSelect, model.id, `${model.label} · ${model.profile}`
  ));
  (aiCatalog.realtime_models?.openai || []).forEach((model) => option(
    realtimeSelect, model.id, `${model.label} · ${model.profile}`
  ));
  (aiCatalog.voices?.openai || []).forEach((voice) => option(
    aiVoiceSelect, voice.id, `${voice.label} · ${voice.profile}`
  ));
  (aiCatalog.reasoning_efforts || []).forEach((effort) => option(
    reasoningSelect, effort.id, `${effort.label} · ${effort.profile}`
  ));
  (aiCatalog.text_verbosities || []).forEach((verbosity) => option(
    agentTextVerbositySelect, verbosity.id, `${verbosity.label} · ${verbosity.profile}`
  ));
  (aiCatalog.routine_reasoning_efforts || []).forEach((effort) => option(
    routineReasoningSelect, effort.id, `${effort.label} · ${effort.profile}`
  ));
  (aiCatalog.realtime_reasoning_efforts || []).forEach((effort) => option(
    realtimeReasoningSelect, effort.id, `${effort.label} · ${effort.profile}`
  ));
  (aiCatalog.realtime_response_styles || []).forEach((style) => option(
    realtimeResponseStyleSelect, style.id, `${style.label} · ${style.profile}`
  ));
  (aiCatalog.agent_models?.ollama || catalog.ai_models || []).forEach((model) => option(
    ollamaSelect, model.id, `${model.id} · ${formatBytes(model.size)}`
  ));
  if (!catalog.voices.length) option(voiceSelect, "", "Nenhuma voz instalada");
  if (!providerSelect.options.length) {
    option(providerSelect, "openai", "OpenAI");
    option(providerSelect, "ollama", "Local (Ollama)");
  }
  if (!ollamaSelect.options.length) option(ollamaSelect, "", "Ollama indisponível");
  renderVoiceLibrary();
  renderUsage();
}

function renderUsage() {
  const usage = catalog.usage || {};
  const today = usage.today || {};
  const month = usage.month || {};
  const requests = Number(today.requests || 0);
  const averageLatency = requests ? Number(today.latency_ms || 0) / requests : 0;
  const format = (value) => new Intl.NumberFormat("pt-BR").format(Number(value || 0));
  $("#usageRequests").textContent = format(requests);
  $("#usageTokens").textContent = format(today.total_tokens);
  $("#usageLatency").textContent = `${Math.round(averageLatency)} ms`;
  $("#usageMonth").textContent = format(month.total_tokens);
  $("#usageNote").textContent = usage.note || "Medição de custo indisponível.";
}

function renderVoiceLibrary() {
  const root = $("#voiceLibrary");
  root.replaceChildren();
  const available = catalog.voices.filter(
    (voice) => voice.official && !voice.installed && voice.downloadable !== false
  );
  const qualities = (catalog.voice_qualities || []).map(qualityLabel);
  $("#qualityNote").textContent = qualities.length ? `Níveis publicados: ${qualities.join(" e ")}` : "Catálogo offline";
  available.forEach((voice) => {
    const row = document.createElement("div");
    row.className = "voice-download";
    const name = document.createElement("strong");
    const quality = document.createElement("small");
    const button = document.createElement("button");
    name.textContent = voice.label;
    quality.textContent = `${voice.profile || qualityLabel(voice.quality)} · ${formatBytes(voice.size)}`;
    button.type = "button";
    button.textContent = "INSTALAR";
    button.dataset.voiceId = voice.id;
    button.addEventListener("click", () => installVoice(voice, button));
    row.append(name, quality, button);
    root.append(row);
  });
  if (!available.length) {
    const note = document.createElement("small");
    note.textContent = catalog.voice_qualities?.length ? "Todas as vozes publicadas já estão instaladas." : "Não foi possível consultar novas vozes agora.";
    root.append(note);
  }
}

function qualityLabel(value) {
  return {low: "leve", medium: "média", high: "alta", custom: "personalizada", neural: "neural humana"}[value] || value;
}

function formatBytes(value) {
  if (!value) return "tamanho local";
  const gigabytes = value / 1024 / 1024 / 1024;
  return gigabytes < 1 ? `${Math.round(value / 1024 / 1024)} MB` : `${gigabytes.toFixed(1)} GB`;
}

function populateSettings(data) {
  aiSettings = data.ai || {};
  $("#ttsModel").value = data.tts.model;
  $("#ttsSpeed").value = data.tts.length_scale;
  $("#sttModel").value = data.stt.model;
  $("#beamSize").value = data.stt.beam_size;
  $("#sttCuda").checked = data.stt.use_cuda;
  const openai = data.ai.profiles.openai;
  const ollama = data.ai.profiles.ollama;
  ensureOption($("#aiProvider"), data.ai.provider);
  ensureOption($("#aiAgentModel"), openai.agent_model);
  ensureOption($("#aiRealtimeModel"), openai.realtime_model);
  ensureOption($("#aiVoice"), openai.voice);
  ensureOption($("#reasoningEffort"), openai.reasoning_effort);
  ensureOption($("#agentTextVerbosity"), openai.agent_text_verbosity);
  ensureOption($("#routineReasoningEffort"), openai.routine_reasoning_effort);
  ensureOption($("#realtimeReasoningEffort"), openai.realtime_reasoning_effort);
  ensureOption($("#realtimeResponseStyle"), openai.realtime_response_style);
  ensureOption($("#ollamaModel"), ollama.model);
  $("#aiProvider").value = data.ai.provider;
  $("#aiAgentModel").value = openai.agent_model;
  $("#aiRealtimeModel").value = openai.realtime_model;
  $("#aiVoice").value = openai.voice;
  $("#reasoningEffort").value = openai.reasoning_effort;
  $("#agentTextVerbosity").value = openai.agent_text_verbosity;
  $("#routineReasoningEffort").value = openai.routine_reasoning_effort;
  $("#realtimeReasoningEffort").value = openai.realtime_reasoning_effort;
  $("#realtimeResponseStyle").value = openai.realtime_response_style;
  $("#ollamaModel").value = ollama.model;
  $("#fallbackEnabled").checked = false;
  ensureOption($("#keepAlive"), ollama.keep_alive);
  $("#keepAlive").value = ollama.keep_alive;
  $("#bargeIn").checked = data.conversation.barge_in;
  $("#bargeWakeword").checked = data.conversation.barge_in_requires_wakeword;
  $("#echoGuard").checked = data.conversation.echo_guard;
  $("#bargeVolume").value = data.conversation.barge_in_volume_percent;
  $("#echoWindow").value = data.conversation.echo_window_seconds;
  $("#conversationTimeout").value = data.conversation.timeout_seconds;
  $("#maxMessages").value = data.conversation.max_messages;
  $("#maxTokens").value = data.conversation.max_tokens;
  $("#micGain").value = data.audio.input_gain_percent;
  $("#micAutoRecover").checked = data.audio.auto_recover;
  updateReadouts();
}

function ensureOption(select, value) {
  if (![...select.options].some((item) => item.value === value)) option(select, value, value);
}

function readSettings() {
  return {
    tts: {model: $("#ttsModel").value, length_scale: Number($("#ttsSpeed").value)},
    stt: {
      model: $("#sttModel").value,
      beam_size: Number($("#beamSize").value),
      use_cuda: $("#sttCuda").checked,
      compute_type: $("#sttCuda").checked ? "float16" : "int8"
    },
    ai: {
      provider: $("#aiProvider").value,
      fallback_provider: "ollama",
      fallback_enabled: false,
      profiles: {
        openai: {
          agent_model: $("#aiAgentModel").value,
          realtime_model: $("#aiRealtimeModel").value,
          voice: $("#aiVoice").value,
          reasoning_effort: $("#reasoningEffort").value,
          agent_text_verbosity: $("#agentTextVerbosity").value,
          routine_reasoning_effort: $("#routineReasoningEffort").value,
          routine_agent_model: aiSettings.profiles?.openai?.routine_agent_model || "gpt-5.6-terra",
          realtime_reasoning_effort: $("#realtimeReasoningEffort").value,
          realtime_max_output_tokens: Number(
            aiSettings.profiles?.openai?.realtime_max_output_tokens || 1024
          ),
          realtime_response_style: $("#realtimeResponseStyle").value
        },
        ollama: {
          url: aiSettings.profiles?.ollama?.url || "http://localhost:11434/api/chat",
          model: $("#ollamaModel").value,
          keep_alive: $("#keepAlive").value
        }
      }
    },
    conversation: {
      timeout_seconds: Number($("#conversationTimeout").value),
      max_messages: Number($("#maxMessages").value),
      max_tokens: Number($("#maxTokens").value),
      barge_in: $("#bargeIn").checked,
      barge_in_requires_wakeword: $("#bargeWakeword").checked,
      echo_guard: $("#echoGuard").checked,
      echo_window_seconds: Number($("#echoWindow").value),
      barge_in_volume_percent: Number($("#bargeVolume").value)
    },
    audio: {
      input_gain_percent: Number($("#micGain").value),
      auto_recover: $("#micAutoRecover").checked
    }
  };
}

function updateReadouts() {
  $("#ttsSpeedOutput").textContent = `${Number($("#ttsSpeed").value).toFixed(2)}×`;
  $("#beamOutput").textContent = $("#beamSize").value;
  $("#bargeVolumeOutput").textContent = `${$("#bargeVolume").value}%`;
  $("#echoWindowOutput").textContent = `${$("#echoWindow").value}s`;
  $("#micGainOutput").textContent = `${$("#micGain").value}%`;
  const model = catalog.stt_models.find((item) => item.id === $("#sttModel").value);
  if (model) {
    $("#sttReadout strong").textContent = model.profile;
    $("#sttReadout small").textContent = `VRAM estimada: ${model.vram_gb} GB`;
  }
  const voice = catalog.voices.find((item) => item.id === $("#ttsModel").value);
  const neural = voice?.engine === "neural";
  const studio = voice?.id === "neural:ptbr-studio";
  $("#ttsBadge").textContent = studio ? "NEURAL RTX" : neural ? "NEURAL CPU" : "PIPER";
  $("#ttsSpeed").disabled = neural;
  $("#ttsReadout strong").textContent = studio
    ? "Naturalidade máxima"
    : neural ? "Natural e responsiva" : "Local e imediato";
  $("#ttsReadout small").textContent = studio
    ? "Libera o Gemma, sintetiza na RTX 5070 e devolve a VRAM antes da reprodução"
    : neural
      ? "Fica aquecida na CPU; Gemma e Whisper continuam residentes na GPU"
      : "Usa pouca memória e permanece como fallback seguro";

  const provider = $("#aiProvider").value || "openai";
  const usingOpenAI = provider === "openai";
  $("#providerBadge").textContent = usingOpenAI ? "OPENAI LIVE" : "OLLAMA LOCAL";
  $("#openaiFields").classList.toggle("hidden", !usingOpenAI);
  $("#ollamaFields").classList.toggle(
    "hidden", usingOpenAI && !$("#fallbackEnabled").checked
  );
  const credential = catalog.ai?.credential || {};
  const readout = $("#providerReadout");
  readout.dataset.status = usingOpenAI && !credential.configured ? "error" : "ready";
  readout.querySelector("strong").textContent = usingOpenAI
    ? (credential.configured ? "Credencial configurada" : "Credencial OpenAI ausente")
    : "Runtime local selecionado";
  readout.querySelector("small").textContent = usingOpenAI
    ? (credential.configured
      ? "Use Testar conexão para validar acesso e modelo"
      : "Configure OPENAI_API_KEY no arquivo .env protegido")
    : "Nenhum dado é enviado ao provedor de nuvem";
}

async function testProviderConnection() {
  const button = $("#testProviderButton");
  const readout = $("#providerReadout");
  button.disabled = true;
  button.textContent = "TESTANDO...";
  readout.dataset.status = "";
  readout.querySelector("strong").textContent = "Conectando ao provedor";
  readout.querySelector("small").textContent = "Validação segura em andamento";
  try {
    const result = await request("/api/admin/test-provider", {
      method: "POST", json: {provider: $("#aiProvider").value}
    });
    readout.dataset.status = result.ok ? "ready" : "error";
    readout.querySelector("strong").textContent = result.ok
      ? "Conexão autorizada"
      : "Provedor indisponível";
    readout.querySelector("small").textContent = result.ok
      ? `${result.models_visible || 0} modelos visíveis · ${result.latency_ms || 0} ms`
      : providerStatusLabel(result.status);
    toast(result.ok ? "Provedor validado com sucesso." : providerStatusLabel(result.status), !result.ok);
  } catch (error) {
    readout.dataset.status = "error";
    readout.querySelector("strong").textContent = "Falha na validação";
    readout.querySelector("small").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "TESTAR CONEXÃO";
  }
}

function providerStatusLabel(status) {
  return {
    credential_missing: "A credencial OpenAI ainda não foi configurada",
    invalid_credential: "A credencial foi rejeitada pela OpenAI",
    access_denied: "O projeto não tem acesso ao recurso solicitado",
    rate_limited: "Limite temporário da API atingido",
    connection_error: "Não foi possível alcançar o provedor",
    unavailable: "O runtime local não respondeu"
  }[status] || "O provedor recusou a validação";
}

function markDirty() {
  const dirty = baseline && JSON.stringify(readSettings()) !== baseline;
  $("#applyButton").disabled = !dirty;
  $("#dirtyIndicator").classList.toggle("dirty", Boolean(dirty));
  $("#dirtyIndicator").lastChild.textContent = dirty ? " Alterações pendentes" : " Sincronizado";
  if (dirty) setApplyState("Alterações prontas", "Revise e aplique quando desejar", "");
  else if (baseline) setApplyState("Configuração sincronizada", "Nenhuma alteração pendente", "");
}

function setApplyState(title, detail, state) {
  $("#applyTitle").textContent = title;
  $("#applyDetail").textContent = detail;
  $("#statusOrb").className = `status-orb ${state || ""}`;
}

async function previewVoice() {
  const button = $("#previewButton");
  button.disabled = true;
  button.textContent = "GERANDO AMOSTRA...";
  try {
    const audio = await request("/api/admin/preview-tts", {
      method: "POST",
      json: {text: $("#previewText").value, model: $("#ttsModel").value, length_scale: Number($("#ttsSpeed").value)}
    });
    const player = $("#previewAudio");
    if (player.dataset.url) URL.revokeObjectURL(player.dataset.url);
    const url = URL.createObjectURL(audio);
    player.dataset.url = url;
    player.src = url;
    await player.play();
    toast("Amostra reproduzida na saída de som atual.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = "<span>▶</span> TESTAR NA CAIXA DE SOM";
  }
}

async function installVoice(voice, button) {
  button.disabled = true;
  button.textContent = "BAIXANDO";
  setApplyState("Instalando nova voz", `${voice.label} · ${qualityLabel(voice.quality)}`, "working");
  try {
    const result = await request("/api/admin/install-voice", {method: "POST", json: {voice_id: voice.id}});
    for (let attempt = 0; attempt < 3000; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 650));
      const payload = await request(`/api/admin/jobs/${encodeURIComponent(result.job_id)}`);
      const job = payload.job;
      setApplyState("Instalando nova voz", job.progress_message || `${Math.round(job.progress || 0)}%`, "working");
      if (job.state === "completed") {
        toast(`${voice.label} está pronta para uso.`);
        await refreshAll();
        return;
      }
      if (["failed", "cancelled"].includes(job.state)) throw new Error(job.error || "Instalação cancelada");
    }
    throw new Error("O download demorou além do esperado");
  } catch (error) {
    setApplyState("Não foi possível instalar", error.message, "error");
    toast(error.message, true);
    button.disabled = false;
    button.textContent = "TENTAR NOVAMENTE";
  }
}

async function applySettings() {
  const button = $("#applyButton");
  button.disabled = true;
  setApplyState("Aplicando configuração", "Preparando atualização segura", "working");
  try {
    const result = await request("/api/admin/apply", {method: "POST", json: {settings: readSettings()}});
    await pollJob(result.job_id);
  } catch (error) {
    setApplyState("Alteração rejeitada", error.message, "error");
    toast(error.message, true);
    button.disabled = false;
  }
}

async function pollJob(jobId) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const payload = await request(`/api/admin/jobs/${encodeURIComponent(jobId)}`);
    const job = payload.job;
    setApplyState(
      job.state === "running" ? "Reconfigurando o Nexo" : "Aguardando subsistema",
      job.progress_message || `Progresso ${Math.round(job.progress || 0)}%`,
      "working"
    );
    if (job.state === "completed") {
      baseline = JSON.stringify(readSettings());
      markDirty();
      setApplyState("Configuração aplicada", "Todos os subsistemas responderam", "");
      toast("Nexo atualizado com segurança.");
      await refreshServices();
      return;
    }
    if (["failed", "cancelled"].includes(job.state)) {
      throw new Error(job.error || "A alteração foi revertida");
    }
  }
  throw new Error("A atualização demorou além do esperado");
}

function renderServices() {
  const root = $("#serviceGrid");
  root.replaceChildren();
  catalog.services.forEach((service) => {
    const card = document.createElement("div");
    card.className = `service ${service.state === "active" ? "active" : ""}`;
    const dot = document.createElement("span");
    dot.className = "service-dot";
    const copy = document.createElement("div");
    const title = document.createElement("b");
    const state = document.createElement("small");
    title.textContent = service.name;
    state.textContent = service.state;
    copy.append(title, state);
    card.append(dot, copy);
    root.append(card);
  });
}

function renderAudioHealth(health) {
  const healthy = Boolean(health?.healthy);
  const badge = $("#micBadge");
  badge.textContent = healthy ? "FLUXO ATIVO" : "RECUPERAÇÃO NECESSÁRIA";
  badge.classList.toggle("cyan", healthy);
  badge.classList.toggle("violet", !healthy);
  $("#micReadout strong").textContent = healthy ? "Microfone operacional" : "Entrada indisponível";
  $("#micReadout small").textContent = healthy
    ? `WSLg conectado · AEC conectado · ganho ${health.input_gain_percent || $("#micGain").value}%`
    : (health?.reason || health?.error || "O fluxo de áudio precisa ser reaplicado");
}

async function refreshAudioHealth() {
  const button = $("#refreshMicButton");
  button.disabled = true;
  try {
    renderAudioHealth(await request("/api/admin/audio-health"));
  } catch (error) {
    renderAudioHealth({healthy: false, error: error.message});
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function repairMicrophone() {
  const button = $("#repairMicButton");
  button.disabled = true;
  setApplyState("Reaplicando microfone", "Reconstruindo WSLg, AEC e listener", "working");
  try {
    const result = await request("/api/admin/repair-audio", {method: "POST", json: {}});
    for (let attempt = 0; attempt < 180; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const payload = await request(`/api/admin/jobs/${encodeURIComponent(result.job_id)}`);
      const job = payload.job;
      setApplyState(
        "Reaplicando microfone",
        job.progress_message || `Progresso ${Math.round(job.progress || 0)}%`,
        "working"
      );
      if (job.state === "completed") {
        setApplyState("Microfone operacional", "Cadeia de entrada reaplicada no Nexo", "");
        toast("Microfone reaplicado com sucesso.");
        await Promise.all([refreshAudioHealth(), refreshServices()]);
        return;
      }
      if (["failed", "cancelled"].includes(job.state)) {
        throw new Error(job.error || "Não foi possível reaplicar o microfone");
      }
    }
    throw new Error("A recuperação do microfone demorou além do esperado");
  } catch (error) {
    setApplyState("Falha no microfone", error.message, "error");
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function refreshServices() {
  const [catalogResult, audioResult] = await Promise.allSettled([
    request("/api/admin/catalog"), request("/api/admin/audio-health")
  ]);
  if (catalogResult.status === "fulfilled") {
    catalog = catalogResult.value;
    renderServices();
    updateReadouts();
  } else toast(catalogResult.reason.message, true);
  if (audioResult.status === "fulfilled") renderAudioHealth(audioResult.value);
  else renderAudioHealth({healthy: false, error: audioResult.reason?.message || "Diagnóstico indisponível"});
}

function selectTab(button) {
  const names = {voice: "Voz & Escuta", intelligence: "Inteligência", conversation: "Conversa", system: "Diagnóstico"};
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.id === `tab-${button.dataset.tab}`));
  $("#sectionTitle").textContent = names[button.dataset.tab];
}

async function logout() {
  try { await request("/api/admin/logout", {method: "POST", json: {}}); } catch (_) { /* expire locally */ }
  lockUI();
}

function lockUI() {
  csrf = "";
  baseline = "";
  $("#app").classList.add("hidden");
  $("#authShell").classList.remove("hidden");
  setAuthMode(true);
  $("#password").focus();
}

$("#authForm").addEventListener("submit", authenticate);
$("#previewButton").addEventListener("click", previewVoice);
$("#applyButton").addEventListener("click", applySettings);
$("#logoutButton").addEventListener("click", logout);
$("#refreshButton").addEventListener("click", refreshServices);
$("#repairMicButton").addEventListener("click", repairMicrophone);
$("#refreshMicButton").addEventListener("click", refreshAudioHealth);
$("#testProviderButton").addEventListener("click", testProviderConnection);
$$(".nav-item").forEach((button) => button.addEventListener("click", () => selectTab(button)));
$$('#app input, #app select, #app textarea').forEach((input) => input.addEventListener("input", () => { updateReadouts(); markDirty(); }));
boot();
