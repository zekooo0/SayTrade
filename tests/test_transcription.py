"""The transcription step: the hints and the conversion.

The model itself is not tested here — it costs money and its answers move. What
is pinned is the machinery around it, which is what the trader's own recording
exposed: the browser's WebM must be converted, and the keywords must be legal.
"""

import io

from app.providers.transcription import to_wav, transcription_keywords


def _a_tiny_webm() -> bytes:
    """A short silent WebM, encoded with the same library the app decodes with,
    so the test needs no recorded fixture."""
    import av

    out = io.BytesIO()
    with av.open(out, mode="w", format="webm") as container:
        stream = container.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        frame = av.AudioFrame(format="s16", layout="mono", samples=4800)
        frame.sample_rate = 48000
        frame.pts = 0
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return out.getvalue()


def test_the_keywords_carry_the_markets_tickers():
    """gpt-transcribe takes keywords — hints, not required output. The trader's
    own tickers are exactly what a misheard word should be matched against:
    "ميت سهم" became "مية سهم" once مية was among them."""
    terms = transcription_keywords(["SWDY", "COMI"])

    assert "SWDY" in terms
    assert "COMI" in terms
    assert "سهم" in terms  # the domain words are always there


def test_a_keyword_never_carries_a_line_break():
    """The API rejects the whole request when a keyword contains a line feed,
    so a ticker with stray whitespace must not be able to break transcription."""
    terms = transcription_keywords(["SW\nDY", "COMI\r", "  "])

    assert all("\n" not in t and "\r" not in t and "<" not in t for t in terms)
    assert "" not in terms


def test_the_browsers_webm_is_converted_to_wav():
    """MediaRecorder writes a streaming WebM with no seek header. whisper-1
    tolerated it; gpt-transcribe rejects it as corrupted. The bytes are decoded
    and re-encoded before they are sent."""
    wav = to_wav(_a_tiny_webm())

    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert len(wav) > 0
