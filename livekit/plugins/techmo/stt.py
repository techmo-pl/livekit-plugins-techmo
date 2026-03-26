"""Techmo ASR speech-to-text plugin for LiveKit Agents."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from dataclasses import dataclass
from typing import AsyncIterator

import grpc
import grpc.aio

from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr

from .log import logger
from .version import __version__

try:
    from . import _proto  # type: ignore[attr-defined]
except ImportError as e:
    raise ImportError(
        "Techmo ASR gRPC stubs not found. "
        "Run 'pip install grpcio-tools && python hatch_build.py' to generate them, "
        "or reinstall the package: 'pip install livekit-plugins-techmo'"
    ) from e


@dataclass
class STTOptions:
    """Configuration options for the Techmo ASR STT plugin."""

    service_address: str
    """gRPC service address in 'host:port' format."""

    sample_rate: int = 16000
    """Audio sample rate in Hz. Default: 16000."""

    language_group: str | None = None
    """Name of the language group of models. Uses service default if None."""

    model_name: str | None = None
    """Name of the specific model. Uses language group default if None."""

    interim_results: bool = True
    """Whether to return interim (partial) results."""

    single_utterance: bool = False
    """If True, stop recognition after the first complete utterance."""

    max_alternatives: int = 1
    """Maximum number of recognition alternatives to return."""

    enable_word_timing: bool = False
    """If True, include word-level timestamps in results."""

    tls: bool = False
    """Use TLS for the gRPC connection."""

    ca_cert: bytes | None = None
    """PEM-encoded CA certificate for TLS verification."""

    client_cert: bytes | None = None
    """PEM-encoded client certificate for mutual TLS."""

    client_key: bytes | None = None
    """PEM-encoded client private key for mutual TLS."""

    grpc_timeout: float | None = None
    """Overall gRPC deadline in seconds. None means no timeout."""

    mrcp_no_input_timeout: int | None = None
    """MRCP no-input-timeout in milliseconds. Finalizes recognition with NO_INPUT_TIMEOUT
    if no speech is detected within this period. None uses the service default."""

    mrcp_recognition_timeout: int | None = None
    """MRCP recognition-timeout in milliseconds. Maximum total duration of an utterance.
    None uses the service default."""

    mrcp_speech_complete_timeout: int | None = None
    """MRCP speech-complete-timeout in milliseconds. Silence duration after speech
    that signals end of utterance when a match is expected. None uses the service default."""

    mrcp_speech_incomplete_timeout: int | None = None
    """MRCP speech-incomplete-timeout in milliseconds. Silence duration after speech
    that signals end of utterance when no match is expected yet. None uses the service default."""


class STT(stt.STT):
    """Techmo ASR speech-to-text provider for LiveKit Agents.

    Connects to a Techmo ASR gRPC service and performs streaming
    speech recognition.

    Example::

        from livekit.plugins.techmo import STT

        stt_engine = STT(service_address="asr.example.com:50051")
        # or with TLS:
        stt_engine = STT(
            service_address="asr.example.com:50051",
            tls=True,
            ca_cert=open("ca.crt", "rb").read(),
        )
    """

    def __init__(
        self,
        *,
        service_address: str | None = None,
        sample_rate: int = 16000,
        language_group: str | None = None,
        model_name: str | None = None,
        interim_results: bool = True,
        single_utterance: bool = False,
        max_alternatives: int = 1,
        enable_word_timing: bool = False,
        tls: bool = False,
        ca_cert: bytes | None = None,
        client_cert: bytes | None = None,
        client_key: bytes | None = None,
        grpc_timeout: float | None = None,
        mrcp_no_input_timeout: int | None = None,
        mrcp_recognition_timeout: int | None = None,
        mrcp_speech_complete_timeout: int | None = None,
        mrcp_speech_incomplete_timeout: int | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=interim_results,
            )
        )

        if service_address is None:
            service_address = os.environ.get("TECHMO_ASR_ADDRESS")
        if not service_address:
            raise ValueError(
                "service_address is required. "
                "Pass it directly or set the TECHMO_ASR_ADDRESS environment variable."
            )

        self._opts = STTOptions(
            service_address=service_address,
            sample_rate=sample_rate,
            language_group=language_group,
            model_name=model_name,
            interim_results=interim_results,
            single_utterance=single_utterance,
            max_alternatives=max_alternatives,
            enable_word_timing=enable_word_timing,
            tls=tls,
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            grpc_timeout=grpc_timeout,
            mrcp_no_input_timeout=mrcp_no_input_timeout,
            mrcp_recognition_timeout=mrcp_recognition_timeout,
            mrcp_speech_complete_timeout=mrcp_speech_complete_timeout,
            mrcp_speech_incomplete_timeout=mrcp_speech_incomplete_timeout,
        )

    def _make_channel(self) -> grpc.aio.Channel:
        opts = self._opts
        if opts.tls or opts.ca_cert or opts.client_cert:
            credentials = grpc.ssl_channel_credentials(
                root_certificates=opts.ca_cert,
                private_key=opts.client_key,
                certificate_chain=opts.client_cert,
            )
            return grpc.aio.secure_channel(opts.service_address, credentials)
        return grpc.aio.insecure_channel(opts.service_address)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
    ) -> stt.SpeechEvent:
        """Perform batch (non-streaming) recognition on a complete audio buffer."""
        config = _build_config(self._opts)
        audio_bytes = b"".join(
            frame.data.tobytes() for frame in buffer
        )

        async with self._make_channel() as channel:
            stub = _proto.AsrStub(channel)

            async def _requests() -> AsyncIterator[_proto.StreamingRecognizeRequest]:  # type: ignore[name-defined]
                yield _proto.StreamingRecognizeRequest(config=config)  # type: ignore[name-defined]
                yield _proto.StreamingRecognizeRequest(  # type: ignore[name-defined]
                    data=_proto.StreamingRecognizeRequestData(  # type: ignore[name-defined]
                        audio=_proto.Audio(bytes=audio_bytes)  # type: ignore[name-defined]
                    )
                )

            alternatives: list[stt.SpeechData] = []
            duration: float = 0.0

            try:
                async for response in stub.StreamingRecognize(
                    _requests(),
                    timeout=self._opts.grpc_timeout,
                ):
                    if response.HasField("result") and response.result.is_final:
                        duration = _proto_duration_to_seconds(response.processed_audio_duration)  # type: ignore[name-defined]
                        alternatives = _parse_alternatives(response.result)
            except grpc.aio.AioRpcError as e:
                logger.error("Techmo ASR recognition failed: %s", e.details())
                raise

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=alternatives or [stt.SpeechData(language="", text="")],
            recognition_usage=stt.RecognitionUsage(audio_duration=duration),
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **kwargs: object,
    ) -> "SpeechStream":
        """Return an async streaming speech recognition context."""
        return SpeechStream(self, self._opts, conn_options=conn_options)


class SpeechStream(stt.SpeechStream):
    """Streaming speech recognition for Techmo ASR.

    Manages a bidirectional gRPC stream: sends audio frames in,
    receives SpeechEvent objects out.
    """

    def __init__(self, stt_instance: STT, opts: STTOptions, *, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=stt_instance, conn_options=conn_options)
        self._opts = opts
        self._done = asyncio.Event()

    async def _run(self) -> None:
        opts = self._opts

        async with stt_instance_channel(opts) as channel:
            stub = _proto.AsrStub(channel)  # type: ignore[name-defined]
            config = _build_config(opts)

            send_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

            async def _request_generator() -> AsyncIterator[_proto.StreamingRecognizeRequest]:  # type: ignore[name-defined]
                yield _proto.StreamingRecognizeRequest(config=config)  # type: ignore[name-defined]
                while True:
                    chunk = await send_queue.get()
                    if chunk is None:
                        return
                    yield _proto.StreamingRecognizeRequest(  # type: ignore[name-defined]
                        data=_proto.StreamingRecognizeRequestData(  # type: ignore[name-defined]
                            audio=_proto.Audio(bytes=chunk)  # type: ignore[name-defined]
                        )
                    )

            recv_task = asyncio.ensure_future(
                self._recv_loop(stub, _request_generator, send_queue)
            )

            resampler: rtc.AudioResampler | None = None

            try:
                async for input_item in self._input_ch:
                    if isinstance(input_item, self._FlushSentinel):
                        logger.debug("SpeechStream: flush received")
                    elif isinstance(input_item, rtc.AudioFrame):
                        frame = input_item
                        if frame.sample_rate != opts.sample_rate or frame.num_channels != 1:
                            if resampler is None or resampler._input_rate != frame.sample_rate:
                                resampler = rtc.AudioResampler(
                                    input_rate=frame.sample_rate,
                                    output_rate=opts.sample_rate,
                                    num_channels=1,
                                )
                            for resampled in resampler.push(frame):
                                await send_queue.put(resampled.data.tobytes())
                        else:
                            await send_queue.put(frame.data.tobytes())
            finally:
                await send_queue.put(None)  # Signal end of input
                await recv_task

    async def _recv_loop(
        self,
        stub: object,
        request_gen_factory: object,
        send_queue: asyncio.Queue[bytes | None],
    ) -> None:
        try:
            async for response in stub.StreamingRecognize(  # type: ignore[union-attr]
                request_gen_factory(),
                timeout=self._opts.grpc_timeout,
            ):
                if not response.HasField("result"):
                    continue

                result = response.result

                if result.HasField("error") and result.error.code != 0:
                    logger.warning(
                        "Techmo ASR error in response: [%d] %s",
                        result.error.code,
                        result.error.message,
                    )
                    continue

                if not result.HasField("speech_recognition_result"):
                    continue

                alts = _parse_alternatives(result)
                if not alts:
                    continue

                duration = _proto_duration_to_seconds(response.processed_audio_duration)

                if result.is_final:
                    logger.debug("Received final transcript: '%s'", alts[0].text if alts else "")
                    event = stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=alts,
                        recognition_usage=stt.RecognitionUsage(audio_duration=duration),
                    )
                else:
                    logger.debug("Received partial transcript: '%s'", alts[0].text if alts else "")
                    event = stt.SpeechEvent(
                        type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                        alternatives=alts,
                    )

                self._event_ch.send_nowait(event)

        except grpc.aio.AioRpcError as e:
            if e.code() not in (grpc.StatusCode.CANCELLED, grpc.StatusCode.OK):
                logger.error("Techmo ASR gRPC stream error: %s", e.details())
                raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class stt_instance_channel:
    """Async context manager wrapping a gRPC channel."""

    def __init__(self, opts: STTOptions) -> None:
        self._opts = opts
        self._channel: grpc.aio.Channel | None = None

    async def __aenter__(self) -> grpc.aio.Channel:
        opts = self._opts
        if opts.tls or opts.ca_cert or opts.client_cert:
            credentials = grpc.ssl_channel_credentials(
                root_certificates=opts.ca_cert,
                private_key=opts.client_key,
                certificate_chain=opts.client_cert,
            )
            self._channel = grpc.aio.secure_channel(opts.service_address, credentials)
        else:
            self._channel = grpc.aio.insecure_channel(opts.service_address)
        return self._channel

    async def __aexit__(self, *_: object) -> None:
        if self._channel:
            await self._channel.close()


def _build_config(opts: STTOptions) -> "_proto.StreamingRecognizeRequestConfig":  # type: ignore[name-defined]
    """Build the initial gRPC config message from plugin options."""
    speech_cfg_kwargs: dict[str, object] = {
        "enable_speech_recognition": True,
        "recognition_alternatives_limit": opts.max_alternatives,
        "enable_time_alignment": opts.enable_word_timing,
    }
    if opts.language_group:
        speech_cfg_kwargs["language_group_name"] = opts.language_group
    if opts.model_name:
        speech_cfg_kwargs["model_name"] = opts.model_name

    config_fields: dict[str, str] = {}
    _MRCP_FIELDS = {
        "no-input-timeout": opts.mrcp_no_input_timeout,
        "recognition-timeout": opts.mrcp_recognition_timeout,
        "speech-complete-timeout": opts.mrcp_speech_complete_timeout,
        "speech-incomplete-timeout": opts.mrcp_speech_incomplete_timeout,
    }
    for key, value in _MRCP_FIELDS.items():
        if value is not None:
            config_fields[key] = str(value)
    if config_fields:
        speech_cfg_kwargs["config_fields"] = config_fields

    return _proto.StreamingRecognizeRequestConfig(  # type: ignore[name-defined]
        audio_config=_proto.AudioConfig(  # type: ignore[name-defined]
            encoding=_proto.AudioConfig.LINEAR16,  # type: ignore[name-defined]
            sampling_rate_hz=float(opts.sample_rate),
        ),
        result_config=_proto.ResultConfig(  # type: ignore[name-defined]
            enable_single_utterance=opts.single_utterance,
            enable_interim_results=opts.interim_results,
        ),
        speech_recognition_config=_proto.SpeechRecognitionConfig(  # type: ignore[name-defined]
            **speech_cfg_kwargs
        ),
    )


def _parse_alternatives(result: object) -> list[stt.SpeechData]:
    """Convert Techmo SpeechRecognitionResult to a list of SpeechData."""
    alts: list[stt.SpeechData] = []
    sr = getattr(result, "speech_recognition_result", None)
    if sr is None:
        return alts

    for alt in sr.recognition_alternatives:
        text = alt.transcript.strip()
        if not text:
            continue
        confidence = alt.confidence if alt.HasField("confidence") else 0.0
        alts.append(stt.SpeechData(language="", text=text, confidence=confidence))

    return alts


def _proto_duration_to_seconds(duration: object) -> float:
    """Convert google.protobuf.Duration to float seconds."""
    secs = getattr(duration, "seconds", 0) or 0
    nanos = getattr(duration, "nanos", 0) or 0
    return float(secs) + nanos / 1e9
