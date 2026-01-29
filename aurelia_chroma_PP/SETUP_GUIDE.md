# 🌸 Aurelia Vale - PersonaPlex Edition Setup Guide 🌸

Welcome! This guide is for the **PersonaPlex Edition** of Aurelia. This version uses the **NVIDIA PersonaPlex** architecture for full-duplex, natural speech interactions and features a dynamic persona module system.

---

## 🚀 Key Differences (PersonaPlex Edition)

- **NVIDIA PersonaPlex Integration:** Uses the Moshi-based full-duplex speech-to-speech model.
- **Dynamic Persona Modules:** Aurelia can autonomously create and modify her own personality traits and voice settings.
- **Hybrid Prompting:** Supports both Text Role Prompts and Voice Audio Prompts (NATF0, NATM1, etc.).

---

## 🛠 Prerequisites

Before we start, you need the standard requirements plus some specific ones for the NVIDIA architecture:

1.  **Python (3.9 or newer)**
2.  **FFmpeg** (For audio handling)
3.  **Libopus-dev:** Required for Moshi audio processing.
    - **Ubuntu/Debian:** `sudo apt install libopus-dev`
    - **Mac:** `brew install opus`
4.  **Hugging Face Account:** You must accept the NVIDIA PersonaPlex model license on Hugging Face to download the weights.

---

## 🤖 Step 1: Configuration

Follow the standard bot setup (get Discord Token, Guild ID, etc.) as described in the base guide.

### Create your .env file
Inside the `aurelia_chroma_PP` folder, create a new file named `.env`:

```env
AURELIA_VALE_DISCORD_TOKEN=your_token_here
AURELIA_VALE_DISCORD_GUILD_ID=your_server_id_here
AURELIA_VALE_DISCORD_VOICE_CHANNEL_ID=your_channel_id_here
HF_TOKEN=your_huggingface_token_here
```

---

## ⚙️ Step 2: Running PersonaPlex

### First Time Setup
The PersonaPlex version requires the `moshi` package. You can install it from the submodule (if available) or via pip:
```bash
pip install -r requirements.txt
```

### Starting Aurelia
Use the included launch scripts:
- **Windows:** `run.bat`
- **Linux/Mac:** `./run.sh`

These scripts are updated to work within the `aurelia_chroma_PP` environment.

---

## 🧠 Using the PersonaPlex System

Aurelia can now manage her own "Persona Modules". You can also manually trigger module changes in chat (or she will do it herself).

### Commands/Tags
Aurelia uses the following internal tags to manage her modules:
- `[PERSONAPLEX: action=create, name=bard, voice=NATF2, data={...}]` - Creates a new module with a specific voice.
- `[PERSONAPLEX: action=activate, name=bard]` - Switches to an existing module.

### Available NVIDIA Voices
- **Natural Female:** NATF0, NATF1, NATF2, NATF3
- **Natural Male:** NATM0, NATM1, NATM2, NATM3
- **Variety:** VARF0-4, VARM0-4

---

## 🖥️ Web Dashboard
View Aurelia's logs and current active PersonaPlex modules at:
`http://localhost:8000`

---

**Treat her with kindness as she evolves!** 🌸
