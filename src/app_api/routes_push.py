"""Fcm device registration and the notifications feed. OWNER: notifications agent."""

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import notify
from app_api.auth import require_device
from app_api.server import ApiError

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])


class FcmRegistration(BaseModel):
    fcm_token: str


@router.post("/devices/fcm")
async def register_fcm(reg: FcmRegistration, device: dict = Depends(require_device)):
    token = reg.fcm_token.strip()
    if not token or len(token) > 4096:
        raise ApiError(400, "bad_token", "That isn't an FCM registration token.")
    await run_in_threadpool(notify.register_fcm_token, token, (device or {}).get("id"))
    return {"ok": True}


@router.get("/notifications")
async def notifications(after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return await run_in_threadpool(notify.feed, after_id, limit)
