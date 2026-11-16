"""Google config, connect (server auth code exchange), disconnect. OWNER: server-core agent."""

from fastapi import APIRouter, Depends

from app_api.auth import require_device

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])
