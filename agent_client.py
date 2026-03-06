from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import VMConfig

log = logging.getLogger("gromitbot-discord.client")

CONNECT_TIMEOUT = 5.0   # seconds to establish TCP connection
READ_TIMEOUT    = 10.0  # seconds to wait for agent response


async def send_command(vm: VMConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """Open a TCP connection to a GromitBot agent, send a JSON command, return the response."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(vm.host, vm.port),
            timeout=CONNECT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Timed out connecting to {vm.name} ({vm.host}:{vm.port})"}
    except OSError as exc:
        return {"ok": False, "error": f"Could not connect to {vm.name} — {exc}"}

    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()

        raw = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
        if not raw:
            return {"ok": False, "error": f"Agent {vm.name} closed connection without a response"}

        return json.loads(raw.decode("utf-8"))

    except asyncio.TimeoutError:
        return {"ok": False, "error": f"Agent {vm.name} did not respond in time"}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Invalid JSON from {vm.name}: {exc}"}
    except OSError as exc:
        return {"ok": False, "error": f"Connection error with {vm.name}: {exc}"}
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2.0)
        except Exception:
            pass
