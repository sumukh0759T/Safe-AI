"""Explicitly enabled, token-protected text conversation test endpoints."""
import hmac
import os
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.conversation import (MAX_TEXT_CHARS, ProviderError,
                                  configured_controller)

router = APIRouter(prefix="/api/v1/voice-test", tags=["voice-test"])
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
controller = configured_controller()


class Turn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


def _authorize(authorization: Optional[str]) -> None:
    expected = os.getenv("SAFEBATH_VOICE_TEST_TOKEN", "")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "voice test is not configured")
    if not hmac.compare_digest(authorization[7:], expected):
        raise HTTPException(401, "invalid authorization")


@router.post("/turn")
async def conversation_turn(body: Turn, authorization: Optional[str] = Header(None)):
    _authorize(authorization)
    if controller is None:
        raise HTTPException(503, "voice test mode or provider configuration is unavailable")
    session_id, text = body.session_id.strip(), body.text.strip()
    if not SESSION_ID.fullmatch(session_id) or not text:
        raise HTTPException(422, "invalid session_id or empty text")
    try:
        reply = await controller.say(session_id, text)
    except ProviderError as exc:
        raise HTTPException(502, f"conversation provider failed ({exc})") from None
    return {"session_id": session_id, "reply_text": reply,
            "kind": "ai_suggestion", "alert_sent": False,
            "audio_playback": "not_available_in_text_test"}


@router.delete("/{session_id}")
def end_conversation(session_id: str, authorization: Optional[str] = Header(None)):
    _authorize(authorization)
    if controller is None:
        raise HTTPException(503, "voice test mode or provider configuration is unavailable")
    if not SESSION_ID.fullmatch(session_id):
        raise HTTPException(422, "invalid session_id")
    return {"session_id": session_id, "ended": controller.end(session_id)}
