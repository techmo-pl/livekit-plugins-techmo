"""Unit tests for the Techmo ASR LiveKit plugin.

These tests mock the gRPC layer so no running Techmo ASR server is needed.
Integration tests require a real server (see tests/test_integration.py).
"""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio_bytes(num_samples: int = 1600, sample_rate: int = 16000) -> bytes:
    """Return raw 16-bit PCM silence."""
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


# ---------------------------------------------------------------------------
# STTOptions
# ---------------------------------------------------------------------------


def test_sttoptions_defaults() -> None:
    from livekit.plugins.techmo.stt import STTOptions

    opts = STTOptions(service_address="localhost:50051")
    assert opts.sample_rate == 16000
    assert opts.interim_results is True
    assert opts.tls is False
    assert opts.max_alternatives == 1


def test_sttoptions_custom() -> None:
    from livekit.plugins.techmo.stt import STTOptions

    opts = STTOptions(
        service_address="asr.example.com:443",
        sample_rate=8000,
        language_group="pl",
        model_name="my-model",
        interim_results=False,
        tls=True,
    )
    assert opts.sample_rate == 8000
    assert opts.language_group == "pl"
    assert opts.model_name == "my-model"
    assert opts.interim_results is False
    assert opts.tls is True


# ---------------------------------------------------------------------------
# STT constructor
# ---------------------------------------------------------------------------


def test_stt_requires_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TECHMO_ASR_ADDRESS", raising=False)
    with pytest.raises(ValueError, match="service_address"):
        # Importing STT triggers proto import; skip if stubs not generated
        try:
            from livekit.plugins.techmo.stt import STT

            STT()
        except ImportError:
            pytest.skip("gRPC stubs not generated")


def test_stt_address_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TECHMO_ASR_ADDRESS", "localhost:50051")
    try:
        from livekit.plugins.techmo.stt import STT

        stt = STT()
        assert stt._opts.service_address == "localhost:50051"
    except ImportError:
        pytest.skip("gRPC stubs not generated")


def test_stt_capabilities() -> None:
    try:
        from livekit.agents import stt as lk_stt
        from livekit.plugins.techmo.stt import STT

        instance = STT(service_address="localhost:50051")
        assert instance.capabilities.streaming is True
        assert instance.capabilities.interim_results is True
    except ImportError:
        pytest.skip("livekit-agents or gRPC stubs not installed")


# ---------------------------------------------------------------------------
# _build_config helper
# ---------------------------------------------------------------------------


def test_build_config_basic() -> None:
    try:
        from livekit.plugins.techmo.stt import STTOptions, _build_config

        opts = STTOptions(service_address="localhost:50051")
        cfg = _build_config(opts)
        assert cfg.audio_config.sampling_rate_hz == 16000.0
        assert cfg.result_config.enable_interim_results is True
        assert cfg.speech_recognition_config.enable_speech_recognition is True
    except ImportError:
        pytest.skip("gRPC stubs not generated")


def test_build_config_with_model() -> None:
    try:
        from livekit.plugins.techmo.stt import STTOptions, _build_config

        opts = STTOptions(
            service_address="localhost:50051",
            language_group="pl",
            model_name="asr-pl-v2",
            max_alternatives=3,
        )
        cfg = _build_config(opts)
        assert cfg.speech_recognition_config.language_group_name == "pl"
        assert cfg.speech_recognition_config.model_name == "asr-pl-v2"
        assert cfg.speech_recognition_config.recognition_alternatives_limit == 3
    except ImportError:
        pytest.skip("gRPC stubs not generated")


# ---------------------------------------------------------------------------
# _proto_duration_to_seconds helper
# ---------------------------------------------------------------------------


def test_proto_duration_to_seconds() -> None:
    try:
        from livekit.plugins.techmo.stt import _proto_duration_to_seconds

        dur = MagicMock()
        dur.seconds = 2
        dur.nanos = 500_000_000
        assert _proto_duration_to_seconds(dur) == pytest.approx(2.5)
    except ImportError:
        pytest.skip("gRPC stubs not generated")


# ---------------------------------------------------------------------------
# session_id (gRPC metadata)
# ---------------------------------------------------------------------------


@pytest.fixture
def stt_module():  # type: ignore[no-untyped-def]
    try:
        from livekit.plugins.techmo import stt as stt_module
    except ImportError:
        pytest.skip("livekit-agents or gRPC stubs not installed")
    return stt_module


class _RecordingStub:
    """Stand-in for AsrStub: records call kwargs and returns an empty response stream."""

    calls: list[dict[str, object]] = []

    def __init__(self, channel: object) -> None:
        pass

    def StreamingRecognize(self, requests: object, **kwargs: object) -> object:  # noqa: N802
        _RecordingStub.calls.append(kwargs)

        async def _responses():  # type: ignore[no-untyped-def]
            return
            yield

        return _responses()


@pytest.fixture
def recording_stub(stt_module, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    _RecordingStub.calls = []
    monkeypatch.setattr(stt_module._proto, "AsrStub", _RecordingStub)
    return _RecordingStub


def test_sttoptions_session_id_default_none(stt_module) -> None:  # type: ignore[no-untyped-def]
    opts = stt_module.STTOptions(service_address="localhost:50051")
    assert opts.session_id is None
    assert stt_module._build_metadata(opts) is None


def test_build_metadata_with_session_id(stt_module) -> None:  # type: ignore[no-untyped-def]
    opts = stt_module.STTOptions(service_address="localhost:50051", session_id="room-42")
    assert stt_module._build_metadata(opts) == (("session-id", "room-42"),)


def test_stt_session_id_empty_means_unset(stt_module) -> None:  # type: ignore[no-untyped-def]
    instance = stt_module.STT(service_address="localhost:50051", session_id="")
    assert instance._opts.session_id is None


@pytest.mark.parametrize("bad", ["zażółć", "line\nbreak", "tab\there"])
def test_stt_session_id_rejects_non_printable_ascii(stt_module, bad: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="session_id"):
        stt_module.STT(service_address="localhost:50051", session_id=bad)


@pytest.mark.parametrize(("session_id", "expected"), [("room-42", (("session-id", "room-42"),)), (None, None)])
async def test_stream_sends_session_id_metadata(stt_module, recording_stub, session_id, expected) -> None:  # type: ignore[no-untyped-def]
    instance = stt_module.STT(service_address="localhost:50051", session_id=session_id)
    stream = instance.stream()
    stream.end_input()

    async def _drain() -> None:
        async for _ in stream:
            pass

    await asyncio.wait_for(_drain(), timeout=5)
    await stream.aclose()

    assert len(recording_stub.calls) == 1
    assert recording_stub.calls[0]["metadata"] == expected


@pytest.mark.parametrize(("session_id", "expected"), [("room-42", (("session-id", "room-42"),)), (None, None)])
async def test_recognize_sends_session_id_metadata(stt_module, recording_stub, session_id, expected) -> None:  # type: ignore[no-untyped-def]
    from livekit import rtc

    instance = stt_module.STT(service_address="localhost:50051", session_id=session_id)
    frame = rtc.AudioFrame(_make_audio_bytes(), sample_rate=16000, num_channels=1, samples_per_channel=1600)

    await instance._recognize_impl([frame])

    assert len(recording_stub.calls) == 1
    assert recording_stub.calls[0]["metadata"] == expected
