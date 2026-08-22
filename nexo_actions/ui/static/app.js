(() => {
  "use strict";

  const canvas = document.querySelector("#hologram");
  const ctx = canvas.getContext("2d", { alpha: true });
  const body = document.body;
  const label = document.querySelector("#stateLabel");
  const detail = document.querySelector("#stateDetail");
  const transcript = document.querySelector("#transcript");
  const sources = document.querySelector("#sources");
  const progress = document.querySelector("#progress");
  const progressBar = progress.querySelector("i");
  const connection = document.querySelector(".connection");
  const connectionLabel = document.querySelector("#connectionLabel");
  const commandForm = document.querySelector("#commandForm");
  const commandInput = document.querySelector("#commandInput");
  const micSensor = document.querySelector("#micSensor");
  const clock = document.querySelector("#clock");

  const modes = {
    idle:       { label: "DISPONÍVEL",  detail: "Diga “Nexo” para começar", energy: .18, speed: .16, color: [83, 232, 255] },
    listening:  { label: "OUVINDO",     detail: "Pode falar naturalmente",   energy: .76, speed: .42, color: [83, 232, 255] },
    thinking:   { label: "PENSANDO",    detail: "Organizando o próximo passo", energy: .5, speed: .9, color: [169, 136, 255] },
    speaking:   { label: "FALANDO",     detail: "Nexo está respondendo",     energy: .94, speed: .55, color: [83, 232, 255] },
    acting:     { label: "EXECUTANDO",  detail: "Cuidando disso no computador", energy: .64, speed: .72, color: [96, 255, 198] },
    confirming: { label: "CONFIRMAÇÃO", detail: "Aguardando sua autorização", energy: .72, speed: .28, color: [255, 177, 91] },
    error:      { label: "ATENÇÃO",     detail: "Algo precisa da sua atenção", energy: .32, speed: .12, color: [255, 85, 122] },
    offline:    { label: "OFFLINE",     detail: "Reconectando ao núcleo local", energy: .09, speed: .08, color: [255, 85, 122] },
  };

  let modeName = "idle";
  let mode = modes.idle;
  let width = 0;
  let height = 0;
  let dpr = 1;
  let pointerX = 0;
  let pointerY = 0;
  let audioLevel = 0;
  let analyser = null;
  let audioData = null;
  let sensorActive = false;
  let previousTime = performance.now();
  let previousPulse = 0;
  let lastBurstAt = 0;
  let stateKick = 0;
  let voiceActive = false;
  const energyBursts = [];
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const particles = [];
  const particleCount = matchMedia("(max-width: 700px)").matches ? 360 : 680;
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < particleCount; i += 1) {
    const y = 1 - (i / (particleCount - 1)) * 2;
    const radius = Math.sqrt(1 - y * y);
    const theta = golden * i;
    particles.push({
      x: Math.cos(theta) * radius,
      y,
      z: Math.sin(theta) * radius,
      phase: Math.random() * Math.PI * 2,
      size: .45 + Math.random() * 1.35,
      drift: .65 + Math.random() * .7,
    });
  }

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = innerWidth;
    height = innerHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function rgba(color, alpha) {
    return `rgba(${color[0]},${color[1]},${color[2]},${alpha})`;
  }

  function voicePulse(now) {
    if (sensorActive && analyser && (modeName === "listening" || voiceActive)) {
      analyser.getByteFrequencyData(audioData);
      let sum = 0;
      for (let i = 2; i < Math.min(audioData.length, 72); i += 1) sum += audioData[i];
      const raw = sum / (70 * 255);
      const target = Math.min(1, raw * 3.8);
      audioLevel += (target - audioLevel) * (target > audioLevel ? .34 : .12);
      return audioLevel;
    }
    const syllables = Math.pow(Math.max(0, Math.sin(now * .0107)), .62);
    const phonemes = Math.abs(Math.sin(now * .0061 + Math.sin(now * .0019) * 2.1));
    const cadence = .58 + Math.sin(now * .00137) * .24;
    const simulated = voiceActive
      ? .2 + syllables * .58 + phonemes * .26 * cadence
      : modeName === "listening" ? .12 + Math.abs(Math.sin(now * .0027)) * .12 : 0;
    const target = reducedMotion ? simulated * .25 : simulated;
    audioLevel += (target - audioLevel) * (target > audioLevel ? .27 : .105);
    return audioLevel;
  }

  function drawArc(cx, cy, radius, start, length, alpha, lineWidth = 1) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius, start, start + length);
    ctx.strokeStyle = rgba(mode.color, alpha);
    ctx.lineWidth = lineWidth;
    ctx.stroke();
  }

  function drawVoiceMembrane(cx, cy, base, now, pulse, energy) {
    if (!voiceActive && modeName !== "listening") return;
    const bands = voiceActive ? 5 : 2;
    for (let band = 0; band < bands; band += 1) {
      const radius = base * (.55 + band * .135);
      const points = 144;
      ctx.beginPath();
      for (let i = 0; i <= points; i += 1) {
        const angle = (i / points) * Math.PI * 2;
        const organic =
          Math.sin(angle * (3 + band) + now * .0042 * (band % 2 ? -1 : 1)) * .48
          + Math.sin(angle * (8 - band) - now * .0071) * .24
          + Math.sin(angle * 13 + now * .0027) * .12;
        const amplitude = base * (voiceActive ? .04 + pulse * .115 : .018 + pulse * .025);
        const r = radius + organic * amplitude * (1 - band * .1);
        const x = cx + Math.cos(angle) * r;
        const y = cy + Math.sin(angle) * r * (.98 + Math.sin(now * .001 + band) * .018);
        if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = rgba(mode.color, .075 + energy * .045 + pulse * (.21 - band * .025));
      ctx.lineWidth = band === 0 ? 1.5 : .72;
      ctx.shadowColor = rgba(mode.color, .45);
      ctx.shadowBlur = voiceActive ? 11 + pulse * 23 : 5;
      ctx.stroke();
      ctx.shadowBlur = 0;
    }
  }

  function drawVoiceRays(cx, cy, base, now, pulse) {
    if (!voiceActive || pulse < .13) return;
    const count = 68;
    ctx.lineCap = "round";
    for (let i = 0; i < count; i += 1) {
      const angle = (i / count) * Math.PI * 2 + Math.sin(now * .0007) * .08;
      const voice = Math.max(0, Math.sin(now * .009 + i * 1.73));
      const inner = base * (1.04 + Math.sin(i * 2.4 + now * .0018) * .012);
      const length = base * (.025 + pulse * (.05 + voice * .15));
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(angle) * inner, cy + Math.sin(angle) * inner);
      ctx.lineTo(cx + Math.cos(angle) * (inner + length), cy + Math.sin(angle) * (inner + length));
      ctx.strokeStyle = rgba(mode.color, .025 + pulse * voice * .17);
      ctx.lineWidth = .55 + voice * .65;
      ctx.stroke();
    }
    ctx.lineCap = "butt";
  }

  function updateAndDrawBursts(cx, cy, base, delta, pulse, now) {
    if (
      voiceActive
      && pulse > .46
      && pulse > previousPulse + .035
      && now - lastBurstAt > 145
      && !reducedMotion
    ) {
      energyBursts.push({ age: 0, strength: Math.min(1, pulse) });
      lastBurstAt = now;
    }
    for (let i = energyBursts.length - 1; i >= 0; i -= 1) {
      const burst = energyBursts[i];
      burst.age += delta / 1000;
      if (burst.age > 1.08) {
        energyBursts.splice(i, 1);
        continue;
      }
      const eased = 1 - Math.pow(1 - burst.age / 1.08, 3);
      const alpha = (1 - eased) * .34 * burst.strength;
      drawArc(cx, cy, base * (.42 + eased * 1.18), 0, Math.PI * 2, alpha, .6 + (1 - eased) * 1.4);
      drawArc(cx, cy, base * (.4 + eased * 1.14), now * .001, Math.PI * .42, alpha * 1.45, 1.1);
    }
    previousPulse = pulse;
  }

  function render(now) {
    const delta = Math.min(40, now - previousTime);
    previousTime = now;
    const pulse = voicePulse(now);
    stateKick *= .93;
    const energy = mode.energy + pulse * .72 + stateKick * .22;
    const cx = width * .5 + pointerX * 7;
    const cy = height * .468 + pointerY * 5;
    const base = Math.min(width * .195, height * .285);
    const breath = 1 + Math.sin(now * .00125) * .025 + pulse * .145 + stateKick * .04;

    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";

    const glow = ctx.createRadialGradient(cx, cy, base * .03, cx, cy, base * 1.68);
    glow.addColorStop(0, rgba(mode.color, .24 * energy));
    glow.addColorStop(.24, rgba(mode.color, .105 * energy));
    glow.addColorStop(.64, rgba(mode.color, .026 + pulse * .025));
    glow.addColorStop(1, rgba(mode.color, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(cx - base * 1.75, cy - base * 1.75, base * 3.5, base * 3.5);

    const nucleus = ctx.createRadialGradient(
      cx - base * .08, cy - base * .1, base * .01,
      cx, cy, base * (.48 + pulse * .14),
    );
    nucleus.addColorStop(0, `rgba(255,255,255,${.08 + pulse * .13})`);
    nucleus.addColorStop(.2, rgba(mode.color, .08 + pulse * .08));
    nucleus.addColorStop(.66, rgba(mode.color, .018 + pulse * .025));
    nucleus.addColorStop(1, rgba(mode.color, 0));
    ctx.fillStyle = nucleus;
    ctx.beginPath();
    ctx.arc(cx, cy, base * (.52 + pulse * .16), 0, Math.PI * 2);
    ctx.fill();

    const rotY = now * .00012 * mode.speed;
    const rotX = Math.sin(now * .00019) * .22 + pointerY * .09;
    const cosY = Math.cos(rotY);
    const sinY = Math.sin(rotY);
    const cosX = Math.cos(rotX);
    const sinX = Math.sin(rotX);
    const projected = [];

    for (const point of particles) {
      const ripple = 1
        + Math.sin(point.phase + now * .0022 * point.drift) * .018
        + Math.sin(point.y * 10 - now * .0035) * .014 * energy
        + pulse * Math.sin(point.phase * 2 + now * .011) * .12
        + pulse * Math.sin(point.y * 17 - now * .008) * .035;
      let x = point.x * ripple;
      let y = point.y * ripple;
      let z = point.z * ripple;
      const rx = x * cosY - z * sinY;
      const rz = x * sinY + z * cosY;
      const ry = y * cosX - rz * sinX;
      z = y * sinX + rz * cosX;
      x = rx;
      y = ry;
      const perspective = 1 / (2.5 - z * .47);
      const scale = base * 2.28 * perspective * breath;
      projected.push({
        x: cx + x * scale,
        y: cy + y * scale,
        z,
        a: .12 + (z + 1) * .23,
        size: point.size * (.65 + (z + 1) * .35) * (1 + pulse * 1.05),
      });
    }

    projected.sort((a, b) => a.z - b.z);
    for (const p of projected) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = rgba(mode.color, p.a * (.55 + energy * .55));
      ctx.fill();
    }

    drawVoiceMembrane(cx, cy, base, now, pulse, energy);
    drawVoiceRays(cx, cy, base, now, pulse);

    const ringTime = now * .00018;
    for (let i = 0; i < 7; i += 1) {
      const r = base * (1.03 + i * .075 + Math.sin(now * .001 + i) * .008);
      const start = ringTime * (i % 2 ? -1 : 1) + i * 1.61;
      drawArc(cx, cy, r, start, .32 + (i % 3) * .18, .08 + energy * .085, i === 0 ? 1.3 + pulse : .7);
      drawArc(cx, cy, r, start + Math.PI, .11 + pulse * .08, .18 + energy * .11, .9 + pulse * .45);
    }

    if (["thinking", "acting", "confirming"].includes(modeName)) {
      const sweep = (now * .0014 * mode.speed) % (Math.PI * 2);
      drawArc(cx, cy, base * 1.22, sweep, .68, .38, 1.2);
      drawArc(cx, cy, base * 1.27, -sweep * .72, .18, .28, .8);
    }

    updateAndDrawBursts(cx, cy, base, delta, pulse, now);

    ctx.restore();
    requestAnimationFrame(render);
  }

  function setState(payload = {}) {
    const requested = modes[payload.state] ? payload.state : modeName;
    if (requested !== modeName) {
      stateKick = 1;
      energyBursts.length = 0;
      previousPulse = 0;
    }
    modeName = requested;
    mode = modes[modeName];
    voiceActive = Boolean(payload.voice_active || modeName === "speaking");
    body.dataset.state = modeName;
    body.classList.toggle("voice-active", voiceActive);
    label.textContent = (payload.label || mode.label).toLocaleUpperCase("pt-BR");
    detail.textContent = payload.detail || (payload.tool ? `Ação: ${payload.tool}` : mode.detail);

    const text = String(payload.text || "").trim();
    transcript.hidden = !text;
    transcript.textContent = text;
    transcript.title = text;

    const sourceItems = Array.isArray(payload.sources) ? payload.sources.slice(0, 6) : [];
    sources.replaceChildren();
    for (const item of sourceItems) {
      if (!item || !String(item.url || "").startsWith("http")) continue;
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = item.domain || item.title || "fonte";
      link.title = item.title || item.url;
      sources.append(link);
    }
    sources.hidden = !sources.childElementCount;

    const hasProgress = Number.isFinite(payload.progress);
    progress.hidden = !hasProgress;
    progressBar.style.width = `${hasProgress ? payload.progress : 0}%`;

    const online = Boolean(payload.connected);
    connection.classList.toggle("online", online);
    connectionLabel.textContent = online ? "NÚCLEO ONLINE" : "RECONECTANDO";
  }

  function connectEvents() {
    const source = new EventSource("/events");
    source.onmessage = event => {
      try { setState(JSON.parse(event.data)); } catch (_) { /* ignore malformed event */ }
    };
    source.onerror = () => {
      connection.classList.remove("online");
      connectionLabel.textContent = "RECONECTANDO";
    };
  }

  commandForm.addEventListener("submit", async event => {
    event.preventDefault();
    const text = commandInput.value.trim();
    if (!text) return;
    commandInput.disabled = true;
    try {
      const response = await fetch("/api/utterance", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Nexo-UI": "local" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) throw new Error((await response.json()).error || "Falha ao enviar");
      commandInput.value = "";
      setState({ state: "thinking", text, connected: true });
    } catch (error) {
      setState({ state: "error", detail: error.message, text, connected: false });
    } finally {
      commandInput.disabled = false;
      commandInput.focus();
    }
  });

  micSensor.addEventListener("click", async () => {
    if (sensorActive) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const audioContext = new AudioContext();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = .74;
      audioData = new Uint8Array(analyser.frequencyBinCount);
      audioContext.createMediaStreamSource(stream).connect(analyser);
      sensorActive = true;
      micSensor.classList.add("active");
      micSensor.querySelector("span").textContent = "SENSOR ATIVO";
    } catch (_) {
      micSensor.querySelector("span").textContent = "SEM PERMISSÃO";
    }
  });

  addEventListener("pointermove", event => {
    pointerX += ((event.clientX / width - .5) - pointerX) * .08;
    pointerY += ((event.clientY / height - .5) - pointerY) * .08;
  }, { passive: true });

  addEventListener("resize", resize, { passive: true });
  setInterval(() => {
    clock.textContent = new Intl.DateTimeFormat("pt-BR", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }).format(new Date());
  }, 500);

  // Useful for local visual QA without exposing a network API.
  window.NexoUI = { setState };
  resize();
  setState({ state: "idle", connected: false });
  connectEvents();
  requestAnimationFrame(render);
})();
