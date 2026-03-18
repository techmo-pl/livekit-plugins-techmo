"""Basic example: run Techmo ASR inside a LiveKit room.

Usage:
    export LIVEKIT_URL=wss://my-livekit-server
    export LIVEKIT_API_KEY=...
    export LIVEKIT_API_SECRET=...
    export TECHMO_ASR_ADDRESS=asr.example.com:5555

    python examples/basic_stt.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins.techmo import STT

logger = logging.getLogger("techmo-example")


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Configure Techmo STT
    # service_address can also be read from TECHMO_ASR_ADDRESS env var
    stt_engine = STT(
        service_address=os.environ.get("TECHMO_ASR_ADDRESS", "localhost:5555"),
        language_group="pl",       # Polish language group — adjust as needed
        interim_results=True,
        sample_rate=16000,
    )

    # Minimal agent: just transcribes and echoes back
    agent = VoicePipelineAgent(
        stt=stt_engine,
        # Add your LLM and TTS here:
        # llm=...,
        # tts=...,
    )

    participant = await ctx.wait_for_participant()
    agent.start(ctx.room, participant)
    logger.info("Techmo ASR agent started for participant %s", participant.identity)

    await asyncio.sleep(3600)  # Keep alive


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
