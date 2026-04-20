# Shiro Discord Integration — Setup Guide

## Files to place in your Shiro-AI folder

| File | Action |
|------|--------|
| `discord_bot.py` | **New** — Place in root of Shiro-AI |
| `discord_bridge.py` | **New** — Place in root of Shiro-AI |
| `main.py` | **Replace** existing main.py |
| `ui/web_gui.py` | **Replace** existing web_gui.py |
| `config.yaml` | **Merge** the new `discord:` block into yours |
| `requirements.txt` | **Replace** or add the discord lines |

---

## Step 1 — Install Discord dependencies

```bash
pip install "discord.py[voice]" PyNaCl numpy
```

On Python 3.13+ you may also need:
```bash
pip install audioop-lts
```

---

## Step 2 — Create your Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it "Shiro" (or anything)
3. Go to **Bot** tab → click **Add Bot**
4. Under **Privileged Gateway Intents**, enable:
   - ✅ Server Members Intent
   - ✅ Message Content Intent
   - ✅ Presence Intent (optional)
5. Copy the **Token** — you'll need it next

---

## Step 3 — Invite your bot to your server

Use this URL (replace CLIENT_ID with your application's ID):

```
https://discord.com/api/oauth2/authorize?client_id=CLIENT_ID&permissions=3146752&scope=bot
```

This grants: **Send Messages**, **Connect**, **Speak**, **Use Voice Activity**

---

## Step 4 — Add token to config.yaml

```yaml
discord:
  enabled: true          # flip this to true
  token: "YOUR_BOT_TOKEN_HERE"   # paste your token here
  auto_start: false      # set true to connect on boot
```

---

## Step 5 — Start Shiro

```bash
python main.py
```

Open the Web GUI → click the **🎮 Discord** tab → click **Connect to Discord**

---

## Using Discord with Shiro

### Text commands (in any channel Shiro can see)
- `!join` — Shiro joins your current voice channel
- `!leave` — Shiro leaves voice
- `!status` — Shows Shiro's connection info

### Natural language (Shiro understands these in chat or voice)
- *"shiro, join the call"* / *"shiro hop in voice"*
- *"shiro leave the discord call please"* / *"shiro disconnect"*
- *"join us in voice, shiro"*

### Voice behavior
- Shiro listens to everyone in the voice channel
- She **only responds** when someone addresses her directly by name
- If two people are talking to each other, she stays quiet and observes
- She'll occasionally comment on things even when not addressed (configurable)
- She can hear multiple speakers — each gets their own identity in her memory

### Sensitivity tuning
In config.yaml, adjust `address_confidence_threshold`:
- `0.4` — More chatty, responds to vaguer mentions
- `0.6` — Default, responds when reasonably sure she's being addressed  
- `0.8` — Only responds when her name is clearly used

---

## Memory & Multi-User

Each Discord user gets their own identity in Shiro's memory system:
- Stored under the key `discord_{user_id}`
- Name tracked as their Discord display name
- Shiro builds a relationship profile per person, same as the local chat

---

## Troubleshooting

**Bot won't connect:**
- Double-check the token in config.yaml
- Make sure `enabled: true` is set
- Check `shiro_ai.log` for the specific error

**No voice / can't hear Shiro:**
- Ensure `tts.enabled: true` and GPT-SoVITS is running
- Confirm PyNaCl installed: `python -c "import nacl; print('ok')"`

**Shiro responds to everything:**
- Raise `address_confidence_threshold` in config (e.g., `0.7`)

**Shiro never responds in voice:**
- Lower `address_confidence_threshold` (e.g., `0.45`)
- Make sure you're saying "shiro" at some point in your message
