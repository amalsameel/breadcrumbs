"""audio.convert: WAV validation, the no-ffmpeg path, and real conversion when ffmpeg is installed."""
from __future__ import annotations

import io
import os
import tempfile
import wave

import pytest

from audio import convert
from audio.convert import ConversionError, ffmpeg_available, to_wav_24k, wav_info


def make_wav(rate: int = 24_000, channels: int = 1, width: int = 2, seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01" * width * channels * int(rate * seconds))
    return buf.getvalue()


def with_list_chunk(wav: bytes) -> bytes:
    """Insert a LIST/INFO metadata chunk before `data`, as recorders often do."""
    info = b"INFOINAM" + (8).to_bytes(4, "little") + b"secret!\x00"
    chunk = b"LIST" + len(info).to_bytes(4, "little") + info
    data_at = wav.index(b"data")
    body = wav[12:data_at] + chunk + wav[data_at:]
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WAVE" + body


def test_wav_info_reads_format_and_duration() -> None:
    assert wav_info(make_wav()) == (1, 24_000, 16, 0.5)
    channels, rate, bits, duration = wav_info(make_wav(rate=48_000, channels=2, seconds=0.25))
    assert (channels, rate, bits) == (2, 48_000, 16) and duration == pytest.approx(0.25)


@pytest.mark.parametrize("data", [b"", b"not audio at all", b"RIFF\x00\x00\x00\x00WAVEjunk"])
def test_wav_info_rejects_garbage(data: bytes) -> None:
    with pytest.raises(ConversionError):
        wav_info(data)


def test_extension_is_sanitized() -> None:
    assert convert._extension("answer.webm") == "webm"
    assert convert._extension("audio/mp4;codecs=mp4a") == "mp4"
    assert convert._extension("ogg") == "ogg"
    assert convert._extension("../../etc/passwd") == "passw"
    assert convert._extension("") == "bin"


async def test_target_wav_passes_through_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert.shutil, "which", lambda _: None)
    src = with_list_chunk(make_wav())
    out = await to_wav_24k(src, "answer.wav")
    assert wav_info(out) == (1, 24_000, 16, 0.5)
    assert b"LIST" not in out and b"secret" not in out  # metadata stripped like -map_metadata -1


async def test_missing_ffmpeg_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert.shutil, "which", lambda _: None)
    assert ffmpeg_available() is False
    with pytest.raises(ConversionError, match="ffmpeg is not installed"):
        await to_wav_24k(b"\x1aE\xdf\xa3" + b"\x00" * 100, "answer.webm")


async def test_ffmpeg_vanishing_after_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert.shutil, "which", lambda _: "/nonexistent/ffmpeg")

    async def boom(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(convert.asyncio, "create_subprocess_exec", boom)
    before = set(os.listdir(tempfile.gettempdir()))
    with pytest.raises(ConversionError, match="ffmpeg is not installed"):
        await to_wav_24k(b"\x1aE\xdf\xa3" + b"\x00" * 100, "answer.webm")
    leftovers = {d for d in set(os.listdir(tempfile.gettempdir())) - before if d.startswith("bc-audio-")}
    assert not leftovers  # temp dir removed even on failure (PRD 15.1)


async def test_empty_input_raises() -> None:
    with pytest.raises(ConversionError, match="empty"):
        await to_wav_24k(b"", "answer.webm")


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed (brew install ffmpeg)")


@needs_ffmpeg
async def test_ffmpeg_resamples_to_24k_mono() -> None:
    out = await to_wav_24k(make_wav(rate=48_000, channels=2, seconds=1.0), "answer.wav")
    channels, rate, bits, duration = wav_info(out)
    assert (channels, rate, bits) == (1, 24_000, 16)
    assert duration == pytest.approx(1.0, abs=0.05)


@needs_ffmpeg
async def test_ffmpeg_rejects_garbage() -> None:
    with pytest.raises(ConversionError, match="ffmpeg failed"):
        await to_wav_24k(b"definitely not audio" * 50, "answer.webm")

