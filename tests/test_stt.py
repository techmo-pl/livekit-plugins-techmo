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
