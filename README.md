# GromitBot-DSCRD

Discord bot controller for [GromitBot](https://github.com/ianncity/GromitBot) — forwards slash commands to one or more GromitBot VM agents over TCP.

## Setup

```powershell
pip install -r requirements.txt
copy .env.example .env   # fill in values
python bot.py
```

## Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from the [Discord Developer Portal](https://discord.com/developers/applications) |
| `AGENT_VMS` | ✅ | Comma-separated `name:host:port` entries, e.g. `vm1:192.168.1.10:9000,vm2:192.168.1.11:9000` |
| `AGENT_SECRET` | — | Shared auth token — must match `GROMITBOT_AGENT_SECRET` on each agent VM |
| `DISCORD_GUILD_ID` | — | Guild ID for instant command sync (recommended during development) |
| `COMMAND_CHANNEL_ID` | — | Restrict bot commands to a specific channel |

## Slash Commands

| Command | Description |
|---|---|
| `/start` `/stop` | Start or stop the bot |
| `/status` | Rich status embed (zone, level, mode, bags, XP/HP/mana) |
| `/list` | All slot statuses on a VM at once |
| `/mode <fishing\|herbalism\|leveling>` | Switch mode |
| `/profile <name>` `/profiles` | Load a leveling profile / list all profiles |
| `/say <text>` | Make the character /say something |
| `/whisper <target> <msg>` | Whisper a player |
| `/emote <emote>` | Perform an emote (e.g. `WAVE`, `DANCE`) |
| `/print <text>` | Print to the in-game chat frame |
| `/mail` | Trigger auto-mail |
| `/jump` `/sit` `/stand` | Player actions |
| `/reload` | ReloadUI() |
| `/disconnect` | /quit the character |
| `/vms` | List all configured VMs |

Every command accepts optional `vm` (VM name) and `bot_slot` (`0`–`7` or `all`) parameters with autocomplete.

## Architecture

```
Discord User  →  /command (slash)
                    │
          GromitBot-DSCRD (this repo)
                    │  TCP JSON :9000
          agent/agent.py  (per VM)
                    │  file I/O
          WoW.exe + GromitBot.dll
                    │  Lua API
          GromitBot Addon (Lua)
```
