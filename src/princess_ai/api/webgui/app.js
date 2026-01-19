const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".tab-panel");
const connectionStatus = document.getElementById("connection-status");
const textPresence = document.getElementById("text-presence");
const voicePresence = document.getElementById("voice-presence");
const textChatWindow = document.getElementById("text-chat-window");
const errorWindow = document.getElementById("error-window");
const voiceStatus = document.getElementById("voice-status");
const voiceParticipants = document.getElementById("voice-participants");
const logSearch = document.getElementById("log-search");
const logWindow = document.getElementById("log-window");
const telemetryListening = document.getElementById("telemetry-listening");
const telemetrySpeaking = document.getElementById("telemetry-speaking");
const telemetryBarge = document.getElementById("telemetry-barge");
const telemetryEmotion = document.getElementById("telemetry-emotion");
const telemetryTranscript = document.getElementById("telemetry-transcript");
const telemetryFinal = document.getElementById("telemetry-final");
const telemetryTokens = document.getElementById("telemetry-tokens");
const telemetryTts = document.getElementById("telemetry-tts");
const metricTtft = document.getElementById("metric-ttft");
const metricE2e = document.getElementById("metric-e2e");
const metricDropped = document.getElementById("metric-dropped");
const metricReconnects = document.getElementById("metric-reconnects");
const adapterTableBody = document.getElementById("adapter-table-body");
const controlStream = document.getElementById("control-stream");
const controlMute = document.getElementById("control-mute");
const controlPersona = document.getElementById("control-persona");
const controlPersonaSet = document.getElementById("control-persona-set");
const controlModel = document.getElementById("control-model");
const controlModelSet = document.getElementById("control-model-set");
const controlManual = document.getElementById("control-manual");
const controlManualSend = document.getElementById("control-manual-send");
const sessionSnapshot = document.getElementById("session-snapshot");
const memoryList = document.getElementById("memory-list");
const memoryEvents = document.getElementById("memory-events");
const memoryRefresh = document.getElementById("memory-refresh");

const state = {
  logs: [],
  errors: [],
  presence: { text: [], voice: [] },
  telemetry: {
    adapters: {},
    voice: {},
    llm_tokens: [],
    qos: {},
    emotion: {},
  },
  session: {},
  controls: {},
  logFilter: "",
};

const setActiveTab = (tabName) => {
  tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === tabName);
  });
  panels.forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tabName}`);
  });
};

tabs.forEach((tab) => {
  tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
});

const formatPresence = (participants) => {
  const count = participants.length;
  return `${count} online${count === 1 ? "" : "s"}`;
};

const renderPresence = () => {
  textPresence.textContent = formatPresence(state.presence.text);
  voicePresence.textContent = formatPresence(state.presence.voice);

  if (state.presence.voice.length === 0) {
    voiceStatus.textContent = "No active voice participants.";
  } else {
    voiceStatus.textContent = "Voice is active.";
  }

  voiceParticipants.innerHTML = "";
  if (state.presence.voice.length === 0) {
    const emptyItem = document.createElement("li");
    emptyItem.textContent = "No one is in voice right now.";
    emptyItem.classList.add("empty");
    voiceParticipants.appendChild(emptyItem);
  } else {
    state.presence.voice.forEach((name) => {
      const item = document.createElement("li");
      item.textContent = name;
      voiceParticipants.appendChild(item);
    });
  }
};

const renderChat = () => {
  textChatWindow.innerHTML = "";
  if (state.logs.length === 0) {
    const empty = document.createElement("div");
    empty.classList.add("empty");
    empty.textContent = "No text chat yet. Join to send presence updates.";
    textChatWindow.appendChild(empty);
    return;
  }

  state.logs
    .filter((entry) => entry.name === "input" || entry.name === "output")
    .slice(-50)
    .forEach((entry) => {
    const message = document.createElement("div");
    message.classList.add("message");
    const label = entry.name === "output" ? "Aurelia" : "User";
    const text = entry.payload?.text ?? entry.payload?.message ?? "(no content)";
    message.innerHTML = `<strong>${label}:</strong> ${text}`;
    textChatWindow.appendChild(message);
  });
};

const renderErrors = () => {
  errorWindow.innerHTML = "";
  if (state.errors.length === 0) {
    const empty = document.createElement("div");
    empty.classList.add("empty");
    empty.textContent = "No errors reported.";
    errorWindow.appendChild(empty);
    return;
  }

  state.errors.slice(-50).forEach((entry) => {
    const message = document.createElement("div");
    message.classList.add("message", "error");
    const logger = entry.payload?.logger ?? "runtime";
    const text = entry.payload?.message ?? "Unknown error";
    message.innerHTML = `<strong>${logger}:</strong> ${text}`;
    errorWindow.appendChild(message);
  });
};

const renderLogs = () => {
  logWindow.innerHTML = "";
  const filter = state.logFilter.toLowerCase();
  const entries = state.logs.filter((entry) => {
    if (!filter) return true;
    return JSON.stringify(entry.payload || {}).toLowerCase().includes(filter);
  });
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.classList.add("empty");
    empty.textContent = "No logs yet.";
    logWindow.appendChild(empty);
    return;
  }
  entries.slice(-80).forEach((entry) => {
    const message = document.createElement("div");
    message.classList.add("message");
    const name = entry.name || "log";
    const payload = entry.payload ? JSON.stringify(entry.payload) : "";
    message.innerHTML = `<strong>${name}:</strong> ${payload}`;
    logWindow.appendChild(message);
  });
};

const renderTelemetry = () => {
  const voice = state.telemetry.voice || {};
  telemetryListening.textContent = voice.listening ? "Yes" : "No";
  telemetrySpeaking.textContent = voice.speaking ? "Yes" : "No";
  telemetryBarge.textContent = voice.barge_in ? "Yes" : "No";
  telemetryTranscript.textContent = voice.last_transcript || "No transcript yet.";
  telemetryFinal.textContent = voice.last_final_transcript || "No final transcript yet.";
  const emotion = state.telemetry.emotion || {};
  telemetryEmotion.textContent = emotion.mood
    ? `${emotion.mood} (${(emotion.valence ?? 0).toFixed(2)})`
    : "warm";
  telemetryTts.innerHTML = "";
  const queue = voice.tts_queue || [];
  if (queue.length === 0) {
    const empty = document.createElement("li");
    empty.classList.add("empty");
    empty.textContent = "TTS queue is empty.";
    telemetryTts.appendChild(empty);
  } else {
    queue.slice(-5).forEach((item) => {
      const row = document.createElement("li");
      row.textContent = item;
      telemetryTts.appendChild(row);
    });
  }

  const tokens = state.telemetry.llm_tokens || [];
  telemetryTokens.textContent = tokens.length ? tokens.join("") : "Waiting for tokens…";

  const qos = state.telemetry.qos || {};
  metricTtft.textContent = qos.time_to_first_token_ms
    ? `${qos.time_to_first_token_ms.toFixed(0)} ms`
    : "–";
  metricE2e.textContent = qos.end_to_end_latency_ms
    ? `${qos.end_to_end_latency_ms.toFixed(0)} ms`
    : "–";
  metricDropped.textContent = qos.dropped_audio_frames ?? 0;
  metricReconnects.textContent = qos.reconnect_count ?? 0;

  adapterTableBody.innerHTML = "";
  const adapters = state.telemetry.adapters || {};
  const names = Object.keys(adapters);
  if (names.length === 0) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="4" class="empty">No adapter status yet.</td>`;
    adapterTableBody.appendChild(row);
    return;
  }
  names.forEach((name) => {
    const adapter = adapters[name];
    const row = document.createElement("tr");
    const lastEvent = adapter.last_event_at
      ? new Date(adapter.last_event_at * 1000).toLocaleTimeString()
      : "–";
    row.innerHTML = `
      <td>${name}</td>
      <td>${adapter.connected ? "Live" : "Offline"}</td>
      <td>${lastEvent}</td>
      <td>${adapter.reconnects ?? 0}</td>
    `;
    adapterTableBody.appendChild(row);
  });
};

const renderSession = () => {
  sessionSnapshot.textContent = JSON.stringify(state.session, null, 2);
};

const renderMemoryEvents = () => {
  memoryEvents.innerHTML = "";
  const entries = state.logs.filter(
    (entry) => entry.name === "memory_saved" || entry.name === "memory_retrieved"
  );
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.classList.add("empty");
    empty.textContent = "No memory events logged.";
    memoryEvents.appendChild(empty);
    return;
  }
  entries.slice(-40).forEach((entry) => {
    const message = document.createElement("div");
    message.classList.add("message");
    const payload = entry.payload || {};
    const label = entry.name === "memory_saved" ? "Saved" : "Retrieved";
    const text = payload.text || (payload.items ? payload.items.join(", ") : "");
    message.innerHTML = `<strong>${label}:</strong> ${text}`;
    memoryEvents.appendChild(message);
  });
};

const syncFromPayload = (payload) => {
  if (Array.isArray(payload.logs)) {
    state.logs = payload.logs;
    state.errors = payload.logs.filter((entry) => entry.name === "error");
  }
  if (Array.isArray(payload.presence)) {
    const presenceMap = { text: [], voice: [] };
    payload.presence.forEach((item) => {
      if (item.channel === "text") {
        presenceMap.text = item.participants || [];
      }
      if (item.channel === "voice") {
        presenceMap.voice = item.participants || [];
      }
    });
    state.presence = presenceMap;
  }
  if (payload.telemetry) {
    state.telemetry = payload.telemetry;
  }
  if (payload.session) {
    state.session = payload.session;
  }
  if (payload.controls) {
    state.controls = payload.controls;
  }
  renderPresence();
  renderChat();
  renderErrors();
  renderLogs();
  renderTelemetry();
  renderSession();
  renderMemoryEvents();
};

const connectWebSocket = () => {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/stream`);

  socket.addEventListener("open", () => {
    connectionStatus.textContent = "Live";
  });

  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(event.data);
      syncFromPayload(payload);
    } catch (error) {
      console.error("Failed to parse websocket payload", error);
    }
  });

  socket.addEventListener("close", () => {
    connectionStatus.textContent = "Disconnected";
    setTimeout(connectWebSocket, 1000);
  });
};

const postPresence = async (endpoint, channel, user) => {
  const params = new URLSearchParams({ channel, user });
  const response = await fetch(`${endpoint}?${params.toString()}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Presence update failed");
  }
  return response.json();
};

const bindPresenceControls = (prefix) => {
  const joinButton = document.getElementById(`${prefix}-join`);
  const leaveButton = document.getElementById(`${prefix}-leave`);
  const input = document.getElementById(`${prefix}-username`);

  joinButton.addEventListener("click", async () => {
    const user = input.value.trim() || "guest";
    await postPresence("/presence/join", prefix, user);
  });

  leaveButton.addEventListener("click", async () => {
    const user = input.value.trim() || "guest";
    await postPresence("/presence/leave", prefix, user);
  });
};

bindPresenceControls("text");
bindPresenceControls("voice");
connectWebSocket();

logSearch.addEventListener("input", (event) => {
  state.logFilter = event.target.value || "";
  renderLogs();
});

const postControl = async (endpoint, params) => {
  const query = new URLSearchParams(params);
  const response = await fetch(`${endpoint}?${query.toString()}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("Control update failed");
  }
  return response.json();
};

controlStream.addEventListener("click", async () => {
  const enabled = !state.controls.stream_mode;
  await postControl("/controls/stream", { enabled });
});

controlMute.addEventListener("click", async () => {
  const enabled = !state.controls.muted;
  await postControl("/controls/mute", { enabled });
});

controlPersonaSet.addEventListener("click", async () => {
  const persona = controlPersona.value.trim();
  if (!persona) return;
  await postControl("/controls/persona", { persona });
});

controlModelSet.addEventListener("click", async () => {
  const model = controlModel.value.trim();
  if (!model) return;
  await postControl("/controls/model", { model });
});

controlManualSend.addEventListener("click", async () => {
  const message = controlManual.value.trim();
  if (!message) return;
  await postControl("/controls/manual", { message });
  controlManual.value = "";
});

const refreshMemories = async () => {
  const response = await fetch("/memories");
  if (!response.ok) {
    throw new Error("Failed to fetch memories");
  }
  const data = await response.json();
  const memories = data.memories || [];
  memoryList.innerHTML = "";
  if (memories.length === 0) {
    const empty = document.createElement("li");
    empty.classList.add("empty");
    empty.textContent = "No memories stored yet.";
    memoryList.appendChild(empty);
    return;
  }
  memories.forEach((memory) => {
    const item = document.createElement("li");
    const scope = memory.scope ? ` · ${memory.scope}` : "";
    item.textContent = `${memory.text} (importance ${memory.importance})${scope}`;
    memoryList.appendChild(item);
  });
};

memoryRefresh.addEventListener("click", () => {
  refreshMemories().catch((error) => console.error(error));
});

refreshMemories().catch((error) => console.error(error));
