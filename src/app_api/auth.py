"""Device pairing and the bearer-token check. OWNER: server-core agent.

Skeleton: require_device lets everything through until the server-core agent
implements tokens (credentials/app_devices.json, hashed). Route modules only
import require_device, so its implementation can change freely.
"""

from fastapi import Request


async def require_device(request: Request) -> None:
    return None
