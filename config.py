from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class VMConfig:
    name: str
    host: str
    port: int

    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"


@dataclass
class Config:
    discord_token: str
    guild_id: Optional[int]
    agent_secret: str
    vms: list[VMConfig]
    command_channel_id: Optional[int]


def load_config() -> Config:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_TOKEN environment variable is required")

    guild_str = os.environ.get("DISCORD_GUILD_ID", "").strip()
    guild_id = int(guild_str) if guild_str else None

    secret = os.environ.get("AGENT_SECRET", "").strip()

    ch_str = os.environ.get("COMMAND_CHANNEL_ID", "").strip()
    command_channel_id = int(ch_str) if ch_str else None

    vms_str = os.environ.get("AGENT_VMS", "vm1:127.0.0.1:9000").strip()
    vms: list[VMConfig] = []
    for entry in vms_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) != 3:
            raise ValueError(
                f"Invalid AGENT_VMS entry {entry!r} — expected name:host:port"
            )
        vms.append(
            VMConfig(
                name=parts[0].strip(),
                host=parts[1].strip(),
                port=int(parts[2].strip()),
            )
        )

    if not vms:
        raise ValueError("AGENT_VMS must contain at least one name:host:port entry")

    return Config(
        discord_token=token,
        guild_id=guild_id,
        agent_secret=secret,
        vms=vms,
        command_channel_id=command_channel_id,
    )
