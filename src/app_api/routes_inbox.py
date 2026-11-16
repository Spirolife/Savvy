"""Inbox, threads, message actions. OWNER: chat-inbox agent."""

from fastapi import APIRouter, Depends

from app_api.auth import require_device

router = APIRouter(prefix="/v1", dependencies=[Depends(require_device)])
