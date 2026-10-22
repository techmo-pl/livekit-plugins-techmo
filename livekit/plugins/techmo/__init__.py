"""LiveKit Agents plugin for Techmo ASR speech-to-text and Techmo TTS text-to-speech."""

from livekit.agents import Plugin

from .log import logger
from .stt import STT, SpeechStream, STTOptions
from .tts import TTS, MarkerEvent, TechmoSynthesizeStream, TTSOptions, VoiceInfo
from .version import __version__

__all__ = [
    "STT",
    "STTOptions",
    "SpeechStream",
    "TTS",
    "TTSOptions",
    "TechmoSynthesizeStream",
    "MarkerEvent",
    "VoiceInfo",
    "__version__",
]

__pdoc__ = {
    "TechmoPlugin": False,
}


class TechmoPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger=logger)

    def download_files(self) -> None:
        pass


Plugin.register_plugin(TechmoPlugin())
