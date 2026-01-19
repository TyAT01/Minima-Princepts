const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".tab-panel");
const connectionStatus = document.getElementById("connection-status");
const textPresence = document.getElementById("text-presence");
const voicePresence = document.getElementById("voice-presence");
const textChatWindow = document.getElementById("text-chat-window");
const errorWindow = document.getElementById("error-window");
const voiceStatus = document.getElementById("voice-status");
const voiceParticipants = document.getElementById("voice-participants");

const state = {
  logs: [],
  errors: [],
  presence: { text: [], voice: [] },
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

  state.logs.slice(-50).forEach((entry) => {
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

const syncFromPayload = (payload) => {
  if (Array.isArray(payload.logs)) {
    state.logs = payload.logs.filter(
      (entry) => entry.name === "input" || entry.name === "output"
    );
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
  renderPresence();
  renderChat();
  renderErrors();
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
