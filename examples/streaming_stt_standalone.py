"""Standalone streaming example — no LiveKit room required.

Reads a WAV file and streams it to Techmo ASR, printing transcripts.

Usage:
    export TECHMO_ASR_ADDRESS=localhost:5555
    python examples/streaming_stt_standalone.py path/to/audio.wav
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import sys
import wave

logger = logging.getLogger(__name__)


def read_wav_pcm(path: str) -> tuple[bytes, int]:
    """Read a WAV file and return (raw_pcm_bytes, sample_rate)."""
    with wave.open(path) as wf:
        if wf.getnchannels() != 1:
            raise ValueError("Only mono WAV files are supported")
        if wf.getsampwidth() != 2:
            raise ValueError("Only 16-bit WAV files are supported")
        sample_rate = wf.getframerate()
        data = wf.readframes(wf.getnframes())
    return data, sample_rate


async def transcribe_file(wav_path: str) -> None:
    try:
        from livekit import rtc
        from livekit.plugins.techmo.stt import STT
    except ImportError as e:
        raise SystemExit(
            "Install dependencies first: pip install livekit-plugins-techmo livekit-rtc"
        ) from e

    address = os.environ.get("TECHMO_ASR_ADDRESS", "localhost:5555")
    pcm, sample_rate = read_wav_pcm(wav_path)
    samples_per_channel = len(pcm) // 2

    frame = rtc.AudioFrame(
        data=pcm,
        sample_rate=sample_rate,
        num_channels=1,
        samples_per_channel=samples_per_channel,
    )

    stt = STT(service_address=address, sample_rate=sample_rate)
    stream = stt.stream()

    await stream.push_frame(frame)
    await stream.end_input()

    print(f"Streaming {wav_path} to {address}...")

    async for event in stream:
        from livekit.agents.stt import SpeechEventType

        if event.type == SpeechEventType.INTERIM_TRANSCRIPT:
            print(f"  [interim] {event.alternatives[0].text}")
        elif event.type == SpeechEventType.FINAL_TRANSCRIPT:
            print(f"  [final]   {event.alternatives[0].text}")

    print("Done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 2:
        print("Usage: python streaming_stt_standalone.py <path/to/audio.wav>")
        sys.exit(1)
    asyncio.run(transcribe_file(sys.argv[1]))
