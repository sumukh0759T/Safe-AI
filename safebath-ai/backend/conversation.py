"""Opt-in, text-only conversational check-in adapter.

This is a prototype path for testing server-side provider configuration. It
does not trigger alerts, control hardware, or claim that a person is safe.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Protocol

import httpx

SYSTEM_INSTRUCTION = (
    "You are a brief bathroom safety check-in assistant. Ask concise questions. "
    "Do not diagnose or claim an alert was sent. If someone says they need help, "
    "tell them to use the available emergency call method while the local system "
    "handles its configured alert rules. Your statements are suggestions, not "
    "confirmed sensor events or notifications."
)
MAX_TEXT_CHARS = 1200
MAX_TURNS = 6
MAX_SESSIONS = 32
SESSION_TTL_SECONDS = 900


class ProviderError(RuntimeError):
    pass


class ConversationProvider(Protocol):
    async def respond(self, history: List[dict]) -> str: ...


class GeminiTextProvider:
    """Gemini generateContent provider; key stays in the Pi environment."""

    def __init__(self, api_key: str, model: str, timeout: float = 12.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def respond(self, history: List[dict]) -> str:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        contents = [{"role": row["role"], "parts": [{"text": row["text"]}]}
                    for row in history]
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url, headers={"x-goog-api-key": self.api_key},
                    json={"systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
                          "contents": contents,
                          "generationConfig": {"maxOutputTokens": 120}},
                )
            response.raise_for_status()
            data = response.json()
            text = "".join(part.get("text", "") for part in
                           data["candidates"][0]["content"]["parts"])
            text = text.strip()
            if not text:
                raise ProviderError("provider returned empty text")
            return text[:MAX_TEXT_CHARS]
        except ProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Avoid including request headers, keys, or response bodies in logs/errors.
            raise ProviderError(type(exc).__name__) from None


@dataclass
class Session:
    history: List[dict] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)


class ConversationController:
    def __init__(self, provider: ConversationProvider):
        self.provider = provider
        self.sessions: "OrderedDict[str, Session]" = OrderedDict()

    async def say(self, session_id: str, text: str) -> str:
        now = time.time()
        self._expire(now)
        session = self.sessions.setdefault(session_id, Session())
        history = (session.history + [{"role": "user", "text": text}])[-MAX_TURNS * 2:]
        # Providers must not retain a mutable alias to the controller's state.
        reply = await self.provider.respond(list(history))
        history.append({"role": "model", "text": reply})
        session.history = history[-MAX_TURNS * 2:]
        session.updated_at = now
        self.sessions.move_to_end(session_id)
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return reply

    def end(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    def _expire(self, now: float) -> None:
        for key in list(self.sessions):
            if now - self.sessions[key].updated_at > SESSION_TTL_SECONDS:
                del self.sessions[key]


def configured_controller():
    if os.getenv("SAFEBATH_VOICE_TEST_MODE", "").lower() != "true":
        return None
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip()
    if not key or not model:
        return None
    return ConversationController(GeminiTextProvider(
        key, model, timeout=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "12"))))
