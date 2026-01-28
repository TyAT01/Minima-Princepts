# 🌸 Aurelia Vale Setup Guide (For Dummies) 🌸

Welcome! This guide will help you get your very own **Aurelia AI** up and running. No master's degree in computer science required!

---

## 🛠 Prerequisites

Before we start, you need two main things installed on your computer:

1.  **Python (3.9 or newer):** The "brain" that runs the code.
2.  **FFmpeg:** A tool that helps Aurelia handle audio (listening and speaking).

### 1. Install Python
- Go to [python.org](https://www.python.org/downloads/) and download the latest version for Windows or Mac.
- **IMPORTANT:** When installing, make sure to check the box that says **"Add Python to PATH"**.

### 2. Install FFmpeg
- **Windows:**
    1. Download the build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/).
    2. Extract it to a folder (like `C:\ffmpeg`).
    3. Add the `bin` folder to your System PATH.
- **Mac:** Open Terminal and type `brew install ffmpeg` (requires Homebrew).
- **Linux:** Type `sudo apt install ffmpeg`.

---

## 🤖 Step 1: Get your Discord Bot Token

Aurelia lives in Discord! You need to "invite" her.

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2.  Click **"New Application"** and name it "Aurelia".
3.  Go to the **"Bot"** tab on the left.
4.  Click **"Reset Token"** (or "Copy Token") to get your **Bot Token**. Save this!
5.  Scroll down to **"Privileged Gateway Intents"** and turn on:
    - Presence Intent
    - Server Members Intent
    - Message Content Intent
6.  Go to the **"OAuth2"** -> **"URL Generator"** tab:
    - Select `bot` and `applications.commands`.
    - Select permissions: `Administrator` (easiest for setup).
    - Copy the URL and paste it into your browser to invite Aurelia to your server.

---

## ⚙️ Step 2: Configuration

Now we need to tell Aurelia which server and channel to join.

1.  In your Discord settings, go to **Advanced** and turn on **Developer Mode**.
2.  Right-click your Server Name and click **"Copy Server ID"** (Guild ID).
3.  Right-click the Voice Channel you want Aurelia to join and click **"Copy Channel ID"**.

### Create your .env file
Inside the `Aurelia_chroma` folder, create a new file named `.env` and paste this into it, replacing the values with your own:

```env
AURELIA_VALE_DISCORD_TOKEN=your_token_here
AURELIA_VALE_DISCORD_GUILD_ID=your_server_id_here
AURELIA_VALE_DISCORD_VOICE_CHANNEL_ID=your_channel_id_here
```

---

## 🚀 Step 3: Starting Aurelia

### Windows
1.  Open the `Aurelia_chroma` folder.
2.  Double-click `run.bat`.
    - It will automatically install everything needed and start the AI!

### Mac / Linux
1.  Open your Terminal.
2.  Navigate to the folder: `cd Aurelia_chroma`
3.  Make the script runnable: `chmod +x run.sh`
4.  Run it: `./run.sh`

---

## 🖥️ Web Dashboard
Once Aurelia is running, you can see what she's thinking in real-time!
- Open your web browser and go to: `http://localhost:8000`
- The `run` scripts are already configured to start the web dashboard for you.

---

## 🧠 Evolution & Learning

Aurelia is designed to learn and evolve from her interactions.

### Autonomous Learning
While running, Aurelia periodically:
- **Reflects** on recent conversations to extract "Lessons Learned".
- Runs **Mental Simulations** when idle to improve her responses in various scenarios (handling irate viewers, lore deep-dives, etc.).

These insights are stored in her long-term memory and will influence her future behavior and responses.

### Standalone Simulations
You can run simulations to help Aurelia learn even when the main bot is not active. This is useful for "training" her on specific scenarios.

To run the standalone simulation script:
```bash
# From the Aurelia_chroma directory
python run_sim.py
```
This will run a series of scenarios, allowing Aurelia to reflect and update her memory store.

---

## ❓ Troubleshooting

- **"Command not found: python"**: Make sure Python is installed and you checked "Add to PATH".
- **"FFmpeg not found"**: Ensure FFmpeg is installed and added to your system's PATH.
- **Audio is choppy**: Aurelia requires a decent computer to run the "Chroma-4B" model. If it's too slow, she might lag.
- **She won't join the channel**: Double-check your Channel ID and make sure the bot has permission to join and speak.

---

**Enjoy your time with Aurelia!** 🌸
