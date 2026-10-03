"""Transcription boundary. The provider/model are recorded on the journal
entry for future quality/cost analysis (spec Q32).

The model is `gpt-transcribe`, not `whisper-1`. Verified on the trader's own
Arabic recording: whisper-1 read "مية سهم" (a hundred shares) as "ميت ساهمة" and
lost the share count before the extractor ever saw it; gpt-transcribe read it
correctly, and with the right keywords read it exactly. whisper-1 is now only
recommended by OpenAI for translating audio into English.
"""

import asyncio
import io
from dataclasses import dataclass
from typing import Protocol, Sequence


from app.log import get_logger

log = get_logger("saytrade.transcription")


@dataclass
class TranscriptionResult:
    transcript: str
    provider: str
    model: str
    duration_ms: int | None = None
    # The language the model detected, when it is confident. Recorded so the app
    # never has to guess whether a note was Arabic or English.
    language: str | None = None


class Transcriber(Protocol):
    async def transcribe(
        self,
        data: bytes,
        mime_type: str,
        keywords: Sequence[str] = (),
        languages: Sequence[str] = (),
    ) -> TranscriptionResult: ...


def to_wav(data: bytes) -> bytes:
    """Decode any supported input to 16 kHz mono WAV.

    The browser records with MediaRecorder, which writes a streaming WebM with no
    seek header. whisper-1 tolerated it; gpt-transcribe rejects it outright
    ("Audio file might be corrupted or unsupported"). The same bytes transcribe
    correctly once decoded here — verified on the trader's own recording.
    """
    import av

    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        out = io.BytesIO()
        with av.open(out, mode="w", format="wav") as target:
            target_stream = target.add_stream("pcm_s16le", rate=16000)
            target_stream.layout = "mono"
            for frame in container.decode(stream):
                resampled = resampler.resample(frame)
                # PyAV returns one frame on older versions and a list on newer.
                pieces = resampled if isinstance(resampled, list) else [resampled]
                for piece in pieces:
                    for packet in target_stream.encode(piece):
                        target.mux(packet)
            for packet in target_stream.encode(None):
                target.mux(packet)
        return out.getvalue()


def transcription_keywords(symbols: Sequence[str] = ()) -> list[str]:
    """The literal terms a note is likely to contain.

    gpt-transcribe takes `keywords` — hints, not required output — and the
    trader's own tickers are exactly what a misheard word should be matched
    against. Verified: "ميت سهم" became "مية سهم" once مية was among them.

    The unit words matter as much as the number words. A price said in piasters
    ("إرش", "قرش") has to come through as that unit, because the extractor
    converts it to the market's currency — a misheard "إرش" turns a price into a
    bare number and, from there, into a price a hundred times too big.

    Each keyword must sit on one line with no <, >, carriage return or line feed,
    or the API rejects the whole request.
    """
    terms = [
        "سهم",
        "جنيه",
        "إرش",
        "قرش",
        "مية",
        "خمسين",
        "اربعين",
        "وقف الخسارة",
        "مستهدف",
    ]
    for symbol in symbols:
        term = symbol.strip().replace("\n", "").replace("\r", "")
        if term and term not in terms:
            terms.append(term)
    return terms


def _detected_language(response) -> str | None:
    """The language gpt-transcribe detected, if it was confident.

    The SDK may not model the field yet, so fall back to the raw payload. An
    empty list means the model could not decide, which is not an error.
    """
    languages = getattr(response, "languages", None)
    if languages is None:
        languages = (getattr(response, "model_extra", None) or {}).get("languages")
    if not languages:
        return None
    first = languages[0]
    code = first.get("code") if isinstance(first, dict) else getattr(first, "code", None)
    return code


class OpenAITranscriber:
    """OpenAI audio.transcriptions with gpt-transcribe.

    Named for the boundary, not the vendor's model, so changing the model stays a
    settings change rather than a code change.
    """

    def __init__(self, api_key: str, model: str = "gpt-transcribe"):
        self.api_key = api_key
        self.model = model

    def _client(self):
        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("OpenAI is not configured (OPENAI_API_KEY)")
        return OpenAI(api_key=self.api_key)

    async def transcribe(
        self,
        data: bytes,
        mime_type: str,
        keywords: Sequence[str] = (),
        languages: Sequence[str] = (),
    ) -> TranscriptionResult:
        client = self._client()
        log.info(
            "transcribe: %s on %d bytes (%s), %d keyword(s), languages=%s",
            self.model,
            len(data),
            mime_type,
            len(keywords),
            list(languages) or "auto",
        )
        wav = await asyncio.to_thread(to_wav, data)
        log.debug("transcribe: converted to %d bytes of WAV", len(wav))

        extra: dict = {}
        if keywords:
            extra["keywords"] = list(keywords)
        if languages:
            # gpt-transcribe takes the plural field; sending `language` as well
            # is rejected, so only one of them is ever sent.
            extra["languages"] = list(languages)

        def _call():
            payload: dict = {
                "model": self.model,
                "file": ("note.wav", wav, "audio/wav"),
            }
            if extra:
                payload["extra_body"] = extra
            return client.audio.transcriptions.create(**payload)  # type: ignore[arg-type]

        response = await asyncio.to_thread(_call)
        result = TranscriptionResult(
            transcript=response.text,
            provider="openai",
            model=self.model,
            language=_detected_language(response),
        )
        log.info(
            "transcribe: %d characters, language=%r",
            len(result.transcript),
            result.language,
        )
        log.info("transcribe: heard %r", result.transcript)
        return result
