"""Transcription boundary. The provider/model are recorded on the journal
entry for future quality/cost analysis (spec Q32)."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from app.providers.storage import extension_for


@dataclass
class TranscriptionResult:
    transcript: str
    provider: str
    model: str
    duration_ms: int | None = None


class Transcriber(Protocol):
    async def transcribe(self, data: bytes, mime_type: str) -> TranscriptionResult: ...


class OpenAIWhisper:
    """OpenAI audio.transcriptions with whisper-1."""

    def __init__(self, api_key: str, model: str = "whisper-1"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY)")
        return OpenAI(api_key=self.api_key)

    async def transcribe(self, data: bytes, mime_type: str) -> TranscriptionResult:
        client = self._client()
        ext = extension_for(mime_type)

        def _call():
            response = client.audio.transcriptions.create(
                model=self.model,
                file=("recording." + ext, data, mime_type),
                response_format="json",
            )
            return response.text

        text = await asyncio.to_thread(_call)
        return TranscriptionResult(
            transcript=text,
            provider="openai",
            model=self.model,
        )