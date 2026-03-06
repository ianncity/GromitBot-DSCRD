"""
bot.py — GromitBot Discord Controller
======================================
Provides slash commands that forward JSON commands to one or more GromitBot
agent VMs over TCP (port 9000 by default).

All commands accept optional `vm` and `bot_slot` parameters:
  vm       — name of the target VM (defaults to the first configured VM)
  bot_slot — integer slot (0–7) or "all" (defaults to agent behaviour:
             slot 0 for single-bot VMs, all slots for multi-bot VMs)

Environment variables — see .env.example for details.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from agent_client import send_command
from config import VMConfig, load_config

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("gromitbot-discord")

cfg = load_config()


# ── Custom CommandTree (channel restriction + centralised error handling) ──────

class GromitTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if cfg.command_channel_id and interaction.channel_id != cfg.command_channel_id:
            await interaction.response.send_message(
                f"❌ GromitBot commands must be used in <#{cfg.command_channel_id}>.",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
        /,
    ) -> None:
        log.error("Slash command error in /%s: %s", interaction.command, error, exc_info=error)
        msg = f"❌ Unexpected error: `{error}`"
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass


# ── Bot ────────────────────────────────────────────────────────────────────────

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=discord.Intents.default(),
    tree_cls=GromitTree,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def resolve_vm(vm_name: Optional[str]) -> VMConfig | str:
    """Return the matching VMConfig, or an error string if not found."""
    if vm_name is None:
        return cfg.vms[0]
    match = next((v for v in cfg.vms if v.name.lower() == vm_name.lower()), None)
    if match is None:
        names = ", ".join(f"`{v.name}`" for v in cfg.vms)
        return f"Unknown VM `{vm_name}`. Available: {names}"
    return match


def build_payload(
    cmd: str,
    args: Optional[str] = None,
    bot_target: Optional[str] = None,
) -> dict:
    """Assemble a JSON payload for the agent."""
    payload: dict = {"cmd": cmd.upper()}
    if args is not None:
        payload["args"] = args
    if bot_target is not None:
        if bot_target.lower() == "all":
            payload["bot"] = "all"
        else:
            payload["bot"] = int(bot_target)   # validated by autocomplete
    if cfg.agent_secret:
        payload["auth"] = cfg.agent_secret
    return payload


def make_status_embed(data: dict, vm: VMConfig, slot_label: str = "") -> discord.Embed:
    running = data.get("running", False)
    colour  = discord.Color.green() if running else discord.Color.red()
    char    = data.get("name", data.get("player", "Unknown"))
    title   = f"GromitBot — {char}" + (f"  [slot {slot_label}]" if slot_label else "")
    embed   = discord.Embed(title=title, colour=colour,
                            description=f"<t:{int(time.time())}:T>")

    def add_field(name: str, value: str) -> None:
        embed.add_field(name=name, value=value or "—", inline=True)

    add_field("VM",     f"{data.get('vm_id', vm.name)}[{data.get('bot_id', 0)}]")
    add_field("Zone",   str(data.get("zone",  "Unknown")))
    add_field("Level",  str(data.get("level", "?")))
    add_field("Mode",   str(data.get("mode",  "unknown")))
    add_field("Status", "🟢 Running" if running else "🔴 Stopped")
    bag = data.get("bagFillPct", data.get("bagFull", 0))
    add_field("Bags",   f"{bag:.0f}%")

    for key in ("xp", "hp", "mana"):
        val = data.get(key)
        if val is not None:
            add_field(key.upper(), str(val))

    err = data.get("error")
    if err and err != "status_unavailable":
        embed.add_field(name="⚠ Error", value=err, inline=False)

    return embed


async def _send(
    interaction: discord.Interaction,
    vm_name: Optional[str],
    cmd: str,
    args: Optional[str] = None,
    bot_target: Optional[str] = None,
) -> None:
    """Core dispatch: resolve VM → build payload → send → format reply."""
    await interaction.response.defer()

    vm = resolve_vm(vm_name)
    if isinstance(vm, str):
        await interaction.followup.send(f"❌ {vm}")
        return

    try:
        payload = build_payload(cmd, args, bot_target)
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}")
        return

    result = await send_command(vm, payload)

    if not result.get("ok"):
        await interaction.followup.send(
            f"❌ **{vm.name}**: {result.get('error', 'Unknown error')}"
        )
        return

    data = result.get("data")

    # LIST response — data.bots is a list of slot statuses
    if isinstance(data, dict) and "bots" in data:
        embeds = [
            make_status_embed(b, vm, slot_label=str(b.get("bot_id", i)))
            for i, b in enumerate(data["bots"])
        ]
        await interaction.followup.send(
            content=f"**{vm.name}** — {data.get('bot_count', '?')} slot(s)",
            embeds=embeds[:10],
        )
        return

    # Single STATUS response
    if isinstance(data, dict):
        embed = make_status_embed(data, vm, slot_label=str(result.get("bot", "")))
        await interaction.followup.send(embed=embed)
        return

    # Broadcast result (multiple slots)
    results = result.get("results")
    if results:
        lines = []
        for slot, res in results.items():
            ok  = "✅" if res.get("ok") else "❌"
            msg = res.get("queued", res.get("error", ""))
            lines.append(f"{ok} Bot {slot}: `{msg}`")
        await interaction.followup.send(f"**{vm.name}**\n" + "\n".join(lines))
        return

    # Simple single-slot queued ack
    queued = result.get("queued", cmd.upper())
    slot   = result.get("bot", "?")
    await interaction.followup.send(
        f"✅ **{vm.name}** bot `{slot}`: `{queued}` queued"
    )


# ── Autocomplete helpers ───────────────────────────────────────────────────────

async def _vm_ac(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=v.name, value=v.name)
        for v in cfg.vms
        if current.lower() in v.name.lower()
    ][:25]


async def _bot_ac(interaction: discord.Interaction, current: str):
    choices = [app_commands.Choice(name="all", value="all")]
    for i in range(8):
        choices.append(app_commands.Choice(name=str(i), value=str(i)))
    return [c for c in choices if current in c.name][:25]


# ── Startup ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    assert bot.user is not None
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    log.info("Configured VMs: %s", ", ".join(str(v) for v in cfg.vms))

    if cfg.guild_id:
        guild = discord.Object(id=cfg.guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info("Synced %d slash commands to guild %d", len(synced), cfg.guild_id)
    else:
        synced = await bot.tree.sync()
        log.info("Synced %d global slash commands (may take up to 1 hour to propagate)", len(synced))


# ── /vms — list all configured VMs ─────────────────────────────────────────────

@bot.tree.command(name="vms", description="List all configured GromitBot VMs")
async def cmd_vms(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="Configured VMs", colour=discord.Color.blurple())
    for vm in cfg.vms:
        embed.add_field(name=vm.name, value=f"`{vm.host}:{vm.port}`", inline=True)
    await interaction.response.send_message(embed=embed)


# ── Simple command factory ─────────────────────────────────────────────────────
# Commands that need only optional `vm` / `bot_slot` parameters are registered
# via _simple() to avoid repeating the same decorator stack 11 times.

def _simple(name: str, description: str, cmd_key: str, *, vm_only: bool = False) -> None:
    """Register a slash command that forwards *cmd_key* with no extra arguments."""
    if vm_only:
        @bot.tree.command(name=name, description=description)
        @app_commands.describe(vm="Target VM (default: first configured VM)")
        @app_commands.autocomplete(vm=_vm_ac)
        async def _handler_vm_only(
            interaction: discord.Interaction,
            vm: Optional[str] = None,
        ) -> None:
            await _send(interaction, vm, cmd_key)
    else:
        @bot.tree.command(name=name, description=description)
        @app_commands.describe(
            vm="Target VM (default: first configured VM)",
            bot_slot="Bot slot 0–7, or 'all'",
        )
        @app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
        async def _handler_with_bot_slot(
            interaction: discord.Interaction,
            vm: Optional[str] = None,
            bot_slot: Optional[str] = None,
        ) -> None:
            await _send(interaction, vm, cmd_key, bot_target=bot_slot)


# ── Bot control ────────────────────────────────────────────────────────────────

_simple("start",  "Start the bot",            "START")
_simple("stop",   "Stop the bot",             "STOP")
_simple("status", "Show current bot status",  "STATUS")
_simple("list",   "List all bot slots and their statuses on a VM", "LIST", vm_only=True)


# ── Mode & profiles ────────────────────────────────────────────────────────────

@bot.tree.command(name="mode", description="Switch the bot mode")
@app_commands.describe(
    mode="Mode to switch to",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.choices(mode=[
    app_commands.Choice(name="fishing",   value="fishing"),
    app_commands.Choice(name="herbalism", value="herbalism"),
    app_commands.Choice(name="leveling",  value="leveling"),
])
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_mode(
    interaction: discord.Interaction,
    mode: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "MODE", args=mode, bot_target=bot_slot)


@bot.tree.command(name="profile", description="Load or hot-swap a leveling profile")
@app_commands.describe(
    name="Profile name",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_profile(
    interaction: discord.Interaction,
    name: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "PROFILE", args=name, bot_target=bot_slot)


_simple("profiles", "List all available leveling profiles", "PROFILES")


# ── Chat / social ──────────────────────────────────────────────────────────────

@bot.tree.command(name="say", description="Make the character /say something in the game")
@app_commands.describe(
    text="Text to say",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_say(
    interaction: discord.Interaction,
    text: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "SAY", args=text, bot_target=bot_slot)


@bot.tree.command(name="whisper", description="Whisper a player in the game")
@app_commands.describe(
    target="Player name to whisper",
    message="Message to send",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_whisper(
    interaction: discord.Interaction,
    target: str,
    message: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "WHISPER", args=f"{target} {message}", bot_target=bot_slot)


@bot.tree.command(name="emote", description="Make the character perform an emote")
@app_commands.describe(
    emote="Emote name (e.g. WAVE, DANCE, CHEER, LAUGH)",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_emote(
    interaction: discord.Interaction,
    emote: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "EMOTE", args=emote, bot_target=bot_slot)


@bot.tree.command(name="print", description="Print a message to the in-game chat frame")
@app_commands.describe(
    text="Text to print",
    vm="Target VM (default: first configured VM)",
    bot_slot="Bot slot 0–7, or 'all'",
)
@app_commands.autocomplete(vm=_vm_ac, bot_slot=_bot_ac)
async def cmd_print(
    interaction: discord.Interaction,
    text: str,
    vm: Optional[str] = None,
    bot_slot: Optional[str] = None,
) -> None:
    await _send(interaction, vm, "PRINT", args=text, bot_target=bot_slot)


# ── Player actions ─────────────────────────────────────────────────────────────

_simple("mail",       "Trigger auto-mail (send items to mailbox character)", "MAIL")
_simple("jump",       "Make the character jump",                             "JUMP")
_simple("sit",        "Make the character sit down",                         "SIT")
_simple("stand",      "Make the character stand up",                         "STAND")


# ── System ─────────────────────────────────────────────────────────────────────

_simple("reload",     "Reload the WoW UI (ReloadUI)",                        "RELOAD")
_simple("disconnect", "Disconnect the character from the game (/quit)",       "DISCONNECT")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(cfg.discord_token, log_handler=None)
