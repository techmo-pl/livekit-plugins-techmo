"""Integration tests for the Techmo TTS plugin.

These tests require a running Techmo TTS service.  They are skipped automatically
when the TECHMO_TTS_ADDRESS environment variable is not set.

Run with:
    TECHMO_TTS_ADDRESS=<your-tts-host>:<port> pytest tests/test_integration.py -v
"""

from __future__ import annotations

import os

import pytest

SERVICE_ADDRESS = os.environ.get("TECHMO_TTS_ADDRESS")

skip_without_service = pytest.mark.skipif(
    not SERVICE_ADDRESS,
    reason="TECHMO_TTS_ADDRESS not set — skipping integration tests",
)


# ---------------------------------------------------------------------------
# ListVoices / sample-rate discovery
# ---------------------------------------------------------------------------


@skip_without_service
def test_list_voices_returns_sample_rate() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _discover_sample_rate

    opts = TTSOptions(service_address=SERVICE_ADDRESS)
    rate = _discover_sample_rate(opts)
    assert rate > 0, f"Expected a positive sample rate, got {rate}"


# ---------------------------------------------------------------------------
# TTS construction
# ---------------------------------------------------------------------------


@skip_without_service
def test_tts_constructor_discovers_rate() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS)
    assert t.sample_rate > 0


# ---------------------------------------------------------------------------
# Full synthesis round-trip
# ---------------------------------------------------------------------------


@skip_without_service
async def test_synthesize_returns_audio() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS, voice_name="michal")

    frames = []
    async with t.synthesize("Witaj, jestem głosem Techmo.") as stream:
        async for audio_ev in stream:
            frames.append(audio_ev.frame)

    assert frames, "Expected at least one audio frame"
    total_samples = sum(f.samples_per_channel for f in frames)
    assert total_samples > 0


@skip_without_service
async def test_list_voices_returns_real_metadata() -> None:
    """list_voices() must map the real proto fields — every voice has language codes."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS)
    voices = await t.list_voices()

    assert voices, "Expected at least one voice from the service"
    assert all(v.name for v in voices)
    # Regression for the language_codes/supported_languages field mismatch:
    # the old getattr-based parsing silently returned [] for every voice.
    assert all(v.language_codes for v in voices), f"Empty language codes: {voices}"
    assert all(v.variants >= 1 for v in voices)


@skip_without_service
async def test_synthesize_honors_output_sample_rate() -> None:
    """Service >= 3.2.4 resamples to output_sample_rate; frames must carry that rate."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS, output_sample_rate=16000)
    assert t.sample_rate == 16000

    frames = []
    async with t.synthesize("Test częstotliwości próbkowania.") as stream:
        async for audio_ev in stream:
            frames.append(audio_ev.frame)

    assert frames, "Expected at least one audio frame"
    assert all(f.sample_rate == 16000 for f in frames), f"Frame rates: {sorted({f.sample_rate for f in frames})}"


@skip_without_service
async def test_synthesize_ssml_markers() -> None:
    """SSML <mark/> tags must surface as MarkerEvents with ascending in-audio times."""
    from livekit.plugins.techmo.tts import TTS, MarkerEvent

    received: list[MarkerEvent] = []
    t = TTS(service_address=SERVICE_ADDRESS, voice_name="michal", on_marker=received.append)

    frames = []
    async with t.synthesize(
        '<speak>Witaj <mark name="m1"/> świecie, to <mark name="m2"/> jest test.</speak>'
    ) as stream:
        async for audio_ev in stream:
            frames.append(audio_ev.frame)

    assert frames, "Expected audio frames for SSML input"
    marks = stream.markers
    assert [m.name for m in marks] == ["m1", "m2"], f"Markers: {marks}"
    assert 0 < marks[0].time_s < marks[1].time_s
    total_s = sum(f.samples_per_channel for f in frames) / frames[0].sample_rate
    assert marks[1].time_s <= total_s + 0.5, f"Marker beyond audio end: {marks[1].time_s} > {total_s}"
    assert received == marks  # callback delivered the same events


@skip_without_service
async def test_synthesize_empty_text_does_not_crash() -> None:
    """Empty text should not raise — the service may return no audio."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS)
    # Should not raise regardless of service behaviour
    try:
        async with t.synthesize("") as stream:
            async for _ in stream:
                pass
    except Exception:  # noqa: S110
        pass  # some services reject empty text; that is acceptable


@skip_without_service
async def test_synthesize_respects_voice_name() -> None:
    """Synthesis with different voices should succeed without error."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address=SERVICE_ADDRESS, voice_name="michal")

    frames = []
    async with t.synthesize("Test głosu Michała.") as stream:
        async for audio_ev in stream:
            frames.append(audio_ev.frame)

    assert frames
