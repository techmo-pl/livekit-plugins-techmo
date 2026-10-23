"""Voice agent using Techmo ASR + Techmo TTS with an OpenAI-compatible LLM.

Requirements:
    pip install "livekit-plugins-techmo @ git+https://github.com/techmo-pl/livekit-plugins-techmo.git@<version-tag>"
    pip install "livekit-agents[openai,silero]"

Environment variables:
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET  LiveKit server and credentials
    TECHMO_ASR_ADDRESS                                Techmo ASR gRPC address (host:port)
    TECHMO_TTS_ADDRESS                                Techmo TTS gRPC address (host:port)
    TECHMO_TTS_VOICE                                  optional, default "michal"
    OPENAI_API_KEY                                    for the LLM

Usage:
    python voice_agent.py dev
"""

from __future__ import annotations

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, silero

from livekit.plugins.techmo import STT, TTS


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        # the room name as session ID lets ASR and TTS service logs be matched to the call
        stt=STT(language_group="pl", interim_results=True, session_id=ctx.room.name),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=TTS(language_code="pl-PL", session_id=ctx.room.name),
    )

    await session.start(
        room=ctx.room,
        agent=Agent(instructions="Jesteś pomocnym asystentem głosowym. Odpowiadasz krótko, po polsku."),
    )
    await session.generate_reply(instructions="Przywitaj użytkownika i zapytaj, w czym możesz pomóc.")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
