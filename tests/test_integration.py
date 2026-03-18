"""Integration tests — require a running Techmo ASR server.

Set TECHMO_ASR_ADDRESS (e.g. 'localhost:5555') to run these tests.
They are automatically skipped when the env var is unset.
"""

from __future__ import annotations

import os
import struct

import pytest

ASR_ADDRESS = os.environ.get("TECHMO_ASR_ADDRESS")
pytestmark = pytest.mark.skipif(
    not ASR_ADDRESS,
    reason="TECHMO_ASR_ADDRESS not set — skipping integration tests",
)


def _silence_pcm(seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    n = int(seconds * sample_rate)
    return struct.pack(f"<{n}h", *([0] * n))


@pytest.mark.asyncio
async def test_streaming_silence() -> None:
    """Send silence and expect either empty results or graceful handling."""
    try:
        from livekit import rtc
        from livekit.plugins.techmo.stt import STT
    except ImportError:
        pytest.skip("livekit-agents or gRPC stubs not installed")

    stt = STT(service_address=ASR_ADDRESS)
    stream = stt.stream()

    pcm = _silence_pcm(1.0)
    frame = rtc.AudioFrame(
        data=pcm,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=len(pcm) // 2,
    )
    await stream.push_frame(frame)
    await stream.end_input()

    events = []
    async for event in stream:
        events.append(event)

    # No crash and stream closes cleanly
    assert isinstance(events, list)
