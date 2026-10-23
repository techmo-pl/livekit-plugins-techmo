"""Techmo TTS text-to-speech plugin for LiveKit Agents."""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import grpc
import grpc.aio
from livekit.agents import APIConnectionError, APITimeoutError, tokenize, tts
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given, shortuuid
from livekit.agents.utils.aio import cancel_and_wait

from .log import logger as _plugin_logger
from .version import __version__

# child of the plugin logger, so TTS records can be told apart from STT ones
logger = _plugin_logger.getChild("tts")

try:
    from tts_service_api.v3 import (
        AudioEncoding,
        ListVoicesRequest,
        OutputConfig,
        ProsodicProperties,
        SynthesisConfig,
        SynthesizeRequest,
        SynthesizeResponse,
        TTSStub,
        Voice,
    )
except ImportError as e:
    raise ImportError(
        "Techmo TTS gRPC stubs (tts-api) not found. Reinstall the plugin, or install them with: "
        "pip install 'tts-api @ git+https://github.com/techmo-pl/tts-api-python.git@v3.2.1'"
    ) from e

_FALLBACK_SAMPLE_RATE = 22050
# Default per-call gRPC deadline when neither conn_options.timeout nor grpc_timeout is set.
# Prevents synthesis calls from hanging indefinitely on a stalled stream.
_DEFAULT_GRPC_TIMEOUT = 30.0


@dataclass
class VoiceInfo:
    """Metadata for a single Techmo TTS voice."""

    name: str
    """Voice identifier (e.g. 'michal', 'ewa')."""

    language_codes: list[str]
    """BCP-47 language codes supported by this voice (e.g. ['pl-PL'])."""

    variants: int = 1
    """Number of available voice style variants."""


@dataclass(frozen=True)
class MarkerEvent:
    """A named SSML ``<mark name="..."/>`` position resolved to audio time.

    The Techmo TTS service reports markers as sample offsets in
    ``SynthesizeResponse.markers``; the plugin converts them to seconds using
    the per-response sample rate. For ``TTS.stream()`` the time is relative to
    the start of the whole stream's audio; for ``TTS.synthesize()`` it is
    relative to the start of that request's audio.
    """

    name: str
    """Marker name from the SSML ``<mark name="..."/>`` tag."""

    time_s: float
    """Marker position in seconds from the start of the stream's audio. This is the
    field to use for scheduling (captions, lip-sync)."""

    position_samples: int
    """Raw sample offset exactly as reported by the service — relative to its own
    gRPC request (i.e. the current sentence in ``TTS.stream()``), NOT to the stream."""


@dataclass
class TTSOptions:
    """Configuration options for the Techmo TTS plugin."""

    service_address: str
    """gRPC service address in 'host:port' format."""

    model: str = "standard"
    """Model/backend identifier (e.g. 'standard'). Returned by the ``model`` property
    for framework telemetry — separate from ``voice_name``."""

    voice_name: str = "michal"
    """Name of the TTS voice to use (e.g. 'michal', 'ewa')."""

    voice_variant: int = 1
    """Voice variant index (1-based)."""

    language_code: str = "pl-PL"
    """BCP-47 language code for synthesis (e.g. 'pl-PL', 'en-US')."""

    pitch: float = 1.0
    """Prosodic pitch multiplier (1.0 = default)."""

    speech_range: float = 1.0
    """Prosodic pitch range multiplier (1.0 = default). Named 'speech_range'
    to avoid shadowing the Python builtin ``range``."""

    rate: float = 1.0
    """Speaking rate multiplier (1.0 = default)."""

    stress: float = 1.0
    """Stress multiplier (1.0 = default)."""

    volume: float = 1.0
    """Volume multiplier (1.0 = default)."""

    tls: bool = False
    """Use TLS for the gRPC connection."""

    ca_cert: bytes | None = None
    """PEM-encoded CA certificate for TLS verification."""

    client_cert: bytes | None = None
    """PEM-encoded client certificate for mutual TLS."""

    client_key: bytes | None = None
    """PEM-encoded client private key for mutual TLS."""

    session_id: str | None = None
    """Session ID sent as gRPC metadata header ``session_id``. ``None`` = header not sent
    (the service assigns its own ID), as in tts-client-python without ``--session-id``."""

    grpc_timeout: float | None = None
    """Per-instance gRPC deadline in seconds. ``None`` defers to conn_options.timeout
    on each call; if that is also default, ``_DEFAULT_GRPC_TIMEOUT`` is used."""

    output_sample_rate: int = 0
    """Requested output sample rate in Hz. ``0`` = service native (default). When non-zero
    it also becomes the plugin's ``sample_rate``. Honoured by the service since 3.2.4;
    older services ignore it (see KNOWN_ISSUES.md) — the audio frames always carry the
    actual rate reported per response."""

    max_frame_size: int = 0
    """Maximum audio frame size in samples per ``SynthesizeResponse``. ``0`` = service
    default. Honoured by the service since 3.2.4; older services ignore it (see
    KNOWN_ISSUES.md)."""

    fallback_sample_rate: int = _FALLBACK_SAMPLE_RATE
    """Sample rate used at construction and when ListVoices discovery fails."""

    session_lifetime_s: float = 300.0
    """Recreate gRPC channel after this many seconds (matches Cartesia default)."""

    pool_size: int = 1
    """Number of parallel gRPC channels. Increase for concurrent synthesis workloads."""

    on_marker: Callable[[MarkerEvent], None] | None = field(default=None, repr=False, compare=False)
    """Optional callback invoked (synchronously, from the event loop) for each SSML
    ``<mark/>`` marker the service reports. Markers only appear when the input text
    contains SSML mark tags. Exceptions raised by the callback are logged, not raised."""


@dataclass
class _ChannelEntry:
    """A single pool slot holding an async gRPC channel and its creation timestamp."""

    channel: grpc.aio.Channel
    created_at: float = field(default_factory=time.monotonic)
    _lifetime_s: float = field(default=300.0, repr=False, compare=False)

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) >= self._lifetime_s


class _ChannelPool:
    """Round-robin pool of async gRPC channels with lifetime-based rotation."""

    def __init__(self, opts: TTSOptions) -> None:
        self._opts = opts
        self._pool_size = opts.pool_size
        self._lifetime_s = opts.session_lifetime_s
        self._entries: list[_ChannelEntry] = []
        self._lock = asyncio.Lock()
        self._idx = 0
        self._closed = False

    async def acquire(self) -> grpc.aio.Channel:
        async with self._lock:
            if self._closed:
                # A late acquire (e.g. a stray synthesis after aclose()) must not
                # silently resurrect channels after teardown.
                raise RuntimeError("Techmo TTS channel pool is closed (aclose() was called)")
            # Fill pool to capacity
            while len(self._entries) < self._pool_size:
                self._entries.append(
                    _ChannelEntry(
                        _make_persistent_channel(self._opts),
                        _lifetime_s=self._lifetime_s,
                    )
                )

            # Rotate expired channel at current index
            entry = self._entries[self._idx % self._pool_size]
            if entry.is_expired():
                await _close_channel_silently(entry.channel)
                entry = _ChannelEntry(
                    _make_persistent_channel(self._opts),
                    _lifetime_s=self._lifetime_s,
                )
                self._entries[self._idx % self._pool_size] = entry

            channel = entry.channel
            self._idx += 1
            return channel

    async def invalidate(self, channel: grpc.aio.Channel) -> None:
        """Force-close a specific channel so the next acquire() recreates it."""
        async with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.channel is channel:
                    await _close_channel_silently(entry.channel)
                    self._entries[i] = _ChannelEntry(
                        _make_persistent_channel(self._opts),
                        _lifetime_s=self._lifetime_s,
                    )
                    break

    async def aclose(self) -> None:
        async with self._lock:
            self._closed = True
            for entry in self._entries:
                await _close_channel_silently(entry.channel)
            self._entries.clear()


async def _close_channel_silently(channel: grpc.aio.Channel) -> None:
    try:
        await channel.close()
    except Exception as e:
        logger.debug("Ignoring error while closing gRPC channel: %s", e)


class TTS(tts.TTS):  # type: ignore[misc, type-arg]
    """Techmo TTS text-to-speech provider for LiveKit Agents.

    Connects to a Techmo TTS gRPC service and synthesises speech.  Sentence-level
    streaming is provided natively via ``TechmoSynthesizeStream`` — callers
    use ``session.start(tts=TTS(...))`` and get streaming without extra setup.

    A single gRPC channel is created on the first synthesis call (or on ``prewarm()``)
    and reused for all subsequent requests.  The channel is automatically recreated
    on transient ``UNAVAILABLE`` errors.

    Example::

        from livekit.plugins.techmo.tts import TTS

        tts_engine = TTS(service_address="tts.example.com:9999")
        # Custom voice, model, and prosody:
        tts_engine = TTS(
            service_address="tts.example.com:9999",
            voice_name="ewa",
            model="standard",
            rate=1.1,
        )
    """

    def __init__(
        self,
        *,
        service_address: str | None = None,
        model: str = "standard",
        voice_name: str | None = None,
        voice_variant: int = 1,
        language_code: str | None = None,
        pitch: float = 1.0,
        speech_range: float = 1.0,
        rate: float = 1.0,
        stress: float = 1.0,
        volume: float = 1.0,
        tls: bool = False,
        ca_cert: bytes | None = None,
        client_cert: bytes | None = None,
        client_key: bytes | None = None,
        ca_cert_file: str | None = None,
        client_cert_file: str | None = None,
        client_key_file: str | None = None,
        session_id: str | None = None,
        grpc_timeout: float | None = None,
        output_sample_rate: int = 0,
        max_frame_size: int = 0,
        fallback_sample_rate: int = _FALLBACK_SAMPLE_RATE,
        session_lifetime_s: float = 300.0,
        pool_size: int = 1,
        on_marker: Callable[[MarkerEvent], None] | None = None,
    ) -> None:
        # --- Resolve service address ---
        if service_address is None:
            service_address = os.environ.get("TECHMO_TTS_ADDRESS")
        if not service_address:
            raise ValueError(
                "service_address is required. Pass it directly or set the TECHMO_TTS_ADDRESS environment variable."
            )

        # --- Resolve optional fields from environment variables ---
        if voice_name is None:
            voice_name = os.environ.get("TECHMO_TTS_VOICE", "michal")
        if language_code is None:
            language_code = os.environ.get("TECHMO_TTS_LANGUAGE", "pl-PL")

        # --- Resolve cert file paths to bytes (bytes args take precedence) ---
        if ca_cert is None and ca_cert_file is not None:
            with open(ca_cert_file, "rb") as fh:
                ca_cert = fh.read()
        if client_cert is None and client_cert_file is not None:
            with open(client_cert_file, "rb") as fh:
                client_cert = fh.read()
        if client_key is None and client_key_file is not None:
            with open(client_key_file, "rb") as fh:
                client_key = fh.read()

        # --- Validate sample-rate configuration ---
        # fallback_sample_rate feeds emitter initialization and marker time math
        # (division); 0 or negative would produce a broken pipeline or ZeroDivisionError.
        if fallback_sample_rate <= 0:
            raise ValueError(f"fallback_sample_rate must be > 0, got {fallback_sample_rate}")
        if output_sample_rate < 0:
            raise ValueError(f"output_sample_rate must be >= 0 (0 = service native), got {output_sample_rate}")

        # --- Warn on surprising TLS configuration ---
        if not tls and (ca_cert is not None or client_cert is not None):
            logger.warning(
                "tls=False but certificate material was provided; TLS will be enabled implicitly. "
                "Pass tls=True to suppress this warning."
            )

        opts = TTSOptions(
            service_address=service_address,
            model=model,
            voice_name=voice_name,
            voice_variant=voice_variant,
            language_code=language_code,
            pitch=pitch,
            speech_range=speech_range,
            rate=rate,
            stress=stress,
            volume=volume,
            tls=tls,
            ca_cert=ca_cert,
            client_cert=client_cert,
            client_key=client_key,
            session_id=session_id or None,
            grpc_timeout=grpc_timeout,
            output_sample_rate=output_sample_rate,
            max_frame_size=max_frame_size,
            fallback_sample_rate=fallback_sample_rate,
            session_lifetime_s=session_lifetime_s,
            pool_size=pool_size,
            on_marker=on_marker,
        )

        _validate_prosody(opts)

        # No blocking network call at construction time. An explicitly requested
        # output_sample_rate wins (the service honours OutputConfig.sampling_rate_hz
        # since 3.2.4); otherwise the fallback rate, which prewarm() verifies against
        # the service's native rate.
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=opts.output_sample_rate or opts.fallback_sample_rate,
            num_channels=1,
        )

        self._opts = opts
        # Channel pool — channels are created lazily on first acquire() and
        # reused across requests; expired or broken channels are rotated automatically.
        self._pool = _ChannelPool(opts)
        # Weak references to in-flight streams (chunked AND native streaming) so
        # aclose() can cancel them gracefully before tearing down the channel pool.
        self._streams: weakref.WeakSet[TechmoChunkedStream | TechmoSynthesizeStream] = weakref.WeakSet()
        # Sentence tokenizer shared by TechmoSynthesizeStream instances.
        self._sentence_tokenizer = tokenize.blingfire.SentenceTokenizer(retain_format=True)
        # Initialised to None; set to an asyncio.Event by prewarm().
        self._prewarm_done: asyncio.Event | None = None
        # Strong reference to the prewarm task — prevents mid-run garbage collection
        # and lets aclose() cancel an in-flight warm-up.
        self._prewarm_task: asyncio.Task[None] | None = None
        # Set by aclose(); prewarm() started after (or concurrently with) teardown
        # must not spawn a task that outlives the closed pool.
        self._closed = False

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "techmo"

    def prewarm(self) -> None:
        """Pre-establish the gRPC channel and verify the service sample rate.

        Schedules async warm-up on the running event loop.  The event
        ``_prewarm_done`` is set when warm-up completes (or fails), allowing
        callers and tests to ``await tts._prewarm_done.wait()`` for readiness.

        Outside a running event loop this is a no-op (warm-up happens on the
        first synthesis call instead); ``_prewarm_done`` is still set.

        Idempotent while a warm-up is already in flight — a second call returns
        without spawning (and without orphaning) another task.
        """
        if self._prewarm_task is not None and not self._prewarm_task.done():
            return
        done_event = asyncio.Event()
        self._prewarm_done = done_event
        if self._closed:
            logger.debug("prewarm() called after aclose(); skipping warm-up.")
            done_event.set()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "prewarm() called without a running event loop; skipping warm-up. "
                "The gRPC channel will be created on the first synthesis call."
            )
            done_event.set()
            return
        self._prewarm_task = loop.create_task(self._prewarm_async(done_event), name="techmo-tts-prewarm")
        # The coroutine's own finally sets the event too, but only once it has started
        # running — a task cancelled before its first step never executes the coroutine
        # body, so waiters would hang. The done-callback covers every termination path.
        self._prewarm_task.add_done_callback(lambda _task: done_event.set())

    async def _prewarm_async(self, done_event: asyncio.Event) -> None:
        try:
            await self._pool.acquire()
            if self._opts.output_sample_rate:
                # An explicit output rate was requested — the service resamples to it
                # (>= 3.2.4), so a differing native rate is expected and not a warning.
                return
            discovered = await asyncio.to_thread(_discover_sample_rate, self._opts)
            if discovered != self.sample_rate:
                logger.warning(
                    "Techmo TTS service reports %d Hz; plugin configured for %d Hz. "
                    "Pass fallback_sample_rate=%d to TTS() to suppress this warning.",
                    discovered,
                    self.sample_rate,
                    discovered,
                )
        except Exception as exc:
            logger.warning(
                "Techmo TTS prewarm failed (%s); continuing with fallback %d Hz",
                exc,
                self.sample_rate,
            )
        finally:
            done_event.set()

    async def _get_channel(self) -> grpc.aio.Channel:
        """Acquire an async gRPC channel from the pool, creating or rotating as needed."""
        return await self._pool.acquire()

    async def _reset_channel(self, channel: grpc.aio.Channel) -> None:
        """Invalidate a specific channel so the pool recreates it on next acquire()."""
        await self._pool.invalidate(channel)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> TechmoChunkedStream:
        stream = TechmoChunkedStream(tts=self, input_text=text, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> TechmoSynthesizeStream:
        """Return a native streaming synthesis context.

        Audio output begins as soon as the first sentence is tokenized,
        while the LLM is still generating subsequent sentences — no
        sentence-level buffering penalty.
        """
        stream = TechmoSynthesizeStream(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def update_options(
        self,
        *,
        model: NotGivenOr[str] = NOT_GIVEN,
        voice_name: NotGivenOr[str] = NOT_GIVEN,
        voice_variant: NotGivenOr[int] = NOT_GIVEN,
        language_code: NotGivenOr[str] = NOT_GIVEN,
        pitch: NotGivenOr[float] = NOT_GIVEN,
        speech_range: NotGivenOr[float] = NOT_GIVEN,
        rate: NotGivenOr[float] = NOT_GIVEN,
        stress: NotGivenOr[float] = NOT_GIVEN,
        volume: NotGivenOr[float] = NOT_GIVEN,
        grpc_timeout: NotGivenOr[float | None] = NOT_GIVEN,
    ) -> None:
        """Update synthesis options in place.

        Changes take effect on the next ``synthesize()`` or ``stream()`` call.
        Only given (non-``NOT_GIVEN``) values are updated.

        ``grpc_timeout=None`` explicitly clears a previously set deadline, deferring
        to ``conn_options.timeout`` or the module default on each call.
        """
        if is_given(model):
            self._opts.model = model
        if is_given(voice_name):
            self._opts.voice_name = voice_name
        if is_given(voice_variant):
            self._opts.voice_variant = voice_variant
        if is_given(language_code):
            self._opts.language_code = language_code
        if is_given(pitch):
            self._opts.pitch = pitch
        if is_given(speech_range):
            self._opts.speech_range = speech_range
        if is_given(rate):
            self._opts.rate = rate
        if is_given(stress):
            self._opts.stress = stress
        if is_given(volume):
            self._opts.volume = volume
        if is_given(grpc_timeout):
            self._opts.grpc_timeout = grpc_timeout

        _validate_prosody(self._opts)

    async def list_voices(self) -> list[VoiceInfo]:
        """Return the list of voices available on the connected service.

        Makes a single ``ListVoices`` gRPC call and returns parsed results.
        The result is NOT cached — call sparingly or cache on your side.

        Raises:
            APIConnectionError: if the gRPC call fails.
        """
        try:
            response = await asyncio.to_thread(self._list_voices_sync)
        except Exception as exc:
            raise APIConnectionError(f"Techmo TTS list_voices failed: {exc}") from exc

        try:
            voices_iter = response.voices  # type: ignore[attr-defined]
        except AttributeError:
            return []

        result: list[VoiceInfo] = []
        try:
            for v in voices_iter:
                # Proto field names (techmo.tts.api.v3.VoiceInfo): supported_languages,
                # variants_count — mapped to the plugin's public VoiceInfo shape.
                result.append(
                    VoiceInfo(
                        name=v.name,
                        language_codes=list(v.supported_languages),
                        variants=int(v.variants_count or 1),
                    )
                )
        except TypeError:
            return []
        return result

    def _list_voices_sync(self) -> object:
        """Call ListVoices synchronously and return the raw response.

        Intended to be called via ``asyncio.to_thread()`` from an async context.
        """
        channel = _make_sync_channel(self._opts)
        stub = TTSStub(channel)
        try:
            return stub.ListVoices(ListVoicesRequest(), timeout=10.0)
        finally:
            channel.close()

    async def aclose(self) -> None:
        # Reject any prewarm() started from here on (see prewarm()).
        self._closed = True
        # Cancel an in-flight prewarm first — it holds pool.acquire() and could
        # otherwise recreate a channel after the pool below has been closed.
        # Await the cancellation so teardown below starts from a settled state.
        if self._prewarm_task is not None and not self._prewarm_task.done():
            self._prewarm_task.cancel()
            await asyncio.gather(self._prewarm_task, return_exceptions=True)
        for stream in list(self._streams):
            await stream.aclose()
        await self._pool.aclose()


class TechmoChunkedStream(tts.ChunkedStream):  # type: ignore[misc]
    """ChunkedStream implementation for a single Techmo TTS synthesis request.

    Calls ``SynthesizeStreaming`` on the gRPC service and pushes each PCM chunk
    to the ``AudioEmitter`` as it arrives.

    A snapshot of the TTS options is taken at construction time so that a
    concurrent ``update_options()`` call cannot corrupt an in-flight synthesis.

    If the gRPC channel is ``UNAVAILABLE`` before the first response arrives, the
    channel is recreated and the call is retried once before raising
    ``APIConnectionError``. After the first response the error is raised immediately
    so the framework can retry with a fresh ``AudioEmitter``.

    The emitter is initialized with the sample rate the service reports in
    ``SynthesizeResponse.sampling_rate_hz`` (falling back to the configured rate),
    so playback speed is correct even when the configured and actual rates differ.
    """

    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: TTS = tts
        # Snapshot opts at construction time — concurrent update_options() calls
        # on the parent TTS will not affect this in-flight request.
        self._opts: TTSOptions = replace(tts._opts)
        self._markers: list[MarkerEvent] = []

    @property
    def markers(self) -> list[MarkerEvent]:
        """SSML ``<mark/>`` events collected so far (times relative to this request's audio)."""
        return list(self._markers)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._opts
        request_id = shortuuid()

        # The framework may retry _run() on this same instance (with a fresh emitter);
        # markers from a failed attempt must not survive into the retry, or consumers
        # would see duplicates. Note the on_marker callback may re-fire for markers a
        # failed attempt already delivered — same at-least-once semantics as the audio.
        self._markers.clear()

        # The emitter is initialized lazily on the first response so it can be stamped
        # with the sample rate the service actually delivers (SynthesizeResponse.
        # sampling_rate_hz) instead of the configured one — otherwise a mismatch (e.g.
        # output_sample_rate requested, or a stale fallback_sample_rate) produces audio
        # played at the wrong speed. end_input()/join() require an initialized emitter,
        # so every non-exception exit path below must call _ensure_initialized().
        initialized = False

        emitter_rate = 0

        def _ensure_initialized(response_rate: int = 0) -> None:
            nonlocal initialized, emitter_rate
            if not initialized:
                emitter_rate = response_rate or self._tts.sample_rate
                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=emitter_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/pcm",
                )
                initialized = True
            elif response_rate and response_rate != emitter_rate:
                # The emitter's rate is fixed at initialization — PCM delivered at a
                # different rate would play at the wrong speed. Fail loudly instead.
                raise APIConnectionError(
                    f"Techmo TTS sample rate changed mid-stream: {emitter_rate} -> {response_rate} Hz"
                )

        # Determine effective per-call deadline (priority order):
        #   1. conn_options.timeout when the caller provided non-default conn_options
        #   2. opts.grpc_timeout when explicitly configured on the TTS instance
        #   3. _DEFAULT_GRPC_TIMEOUT to prevent infinite hangs
        if self._conn_options != DEFAULT_API_CONNECT_OPTIONS:
            timeout = self._conn_options.timeout
        elif opts.grpc_timeout is not None:
            timeout = opts.grpc_timeout
        else:
            timeout = _DEFAULT_GRPC_TIMEOUT

        request = _build_request(self._input_text, opts)
        metadata = _build_metadata(opts)

        for attempt in range(2):
            channel: grpc.aio.Channel | None = None
            try:
                channel = await self._tts._get_channel()
                stub = TTSStub(channel)
                async for response in stub.SynthesizeStreaming(
                    request,
                    metadata=metadata,
                    timeout=timeout,
                ):
                    _ensure_initialized(response.sampling_rate_hz)
                    # emitter_rate (not the configured fallback) resolves a mid-stream
                    # sampling_rate_hz=0: once established, the emitter rate is the
                    # single source of truth for all timing math.
                    _emit_markers(response, emitter_rate, 0.0, self._markers, opts.on_marker)
                    if response.audio:
                        output_emitter.push(response.audio)
                _ensure_initialized()
                output_emitter.flush()
                return
            except grpc.aio.AioRpcError as e:
                if e.code() == grpc.StatusCode.UNAVAILABLE:
                    # Channel may be in a broken state — invalidate it so the next
                    # acquire() (ours or a framework-level retry) gets a fresh one.
                    if channel is not None:
                        await self._tts._reset_channel(channel)
                    if attempt == 0 and not initialized:
                        logger.warning(
                            "Techmo TTS channel UNAVAILABLE; reconnecting and retrying (attempt %d)...",
                            attempt + 1,
                        )
                        continue
                    # A response was already received — the emitter is initialized and may
                    # hold audio, so an internal retry would double-initialize or duplicate
                    # it. Raise instead; the framework retries with a fresh emitter.
                    raise APIConnectionError(f"Techmo TTS gRPC error [{e.code()}]: {e.details()}") from e
                if e.code() in (grpc.StatusCode.CANCELLED, grpc.StatusCode.OK):
                    # Session ended or RPC cancelled by the service — treated as a
                    # graceful end (v0.1.1 design), but the output may be truncated,
                    # so leave a trace. The emitter must still be initialized for
                    # end_input()/join().
                    logger.warning(
                        "Techmo TTS synthesis ended early by the service [%s] — output may be truncated: %s",
                        e.code(),
                        e.details(),
                    )
                    _ensure_initialized()
                    return
                if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                    raise APITimeoutError(f"Techmo TTS timed out: {e.details()}") from e
                raise APIConnectionError(f"Techmo TTS gRPC error [{e.code()}]: {e.details()}") from e

    async def cancel(self) -> None:
        """Abort in-flight synthesis immediately.

        Closes the stream and releases the gRPC connection.  Safe to call
        multiple times.  After cancellation no further audio frames will be
        emitted by this stream instance.

        Typical voicebot use: call ``await stream.cancel()`` when a barge-in
        event is detected while this stream is producing audio.
        """
        await self.aclose()


class TechmoSynthesizeStream(tts.SynthesizeStream):  # type: ignore[misc]
    """Native sentence-level streaming synthesis for Techmo TTS.

    Runs two concurrent tasks:
    - ``_forward_input``: reads text tokens from ``self._input_ch`` and feeds
      them into a sentence tokenizer stream; flushes on ``_FlushSentinel``.
    - ``_synthesize``: consumes complete sentences from the tokenizer and
      issues a ``SynthesizeStreaming`` gRPC call per sentence, pushing PCM
      chunks to the ``AudioEmitter`` as they arrive.

    Because both tasks run concurrently, audio for sentence N begins
    streaming while sentence N+1 is still being tokenized — eliminating the
    full-sentence buffering penalty of ``StreamAdapter``.
    """

    def __init__(
        self,
        *,
        tts: TTS,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts
        self._markers: list[MarkerEvent] = []
        self._run_attempts = 0

    @property
    def markers(self) -> list[MarkerEvent]:
        """SSML ``<mark/>`` events collected so far (times relative to the stream's audio)."""
        return list(self._markers)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # The framework retries _run() on this same instance, but the text input
        # (self._input_ch) is a one-shot channel already drained by the first attempt —
        # a retry can only produce an empty run and a confusing "no audio frames"
        # error after burning the retry budget. Fail fast with an honest message;
        # transient gRPC failures are already retried internally per sentence.
        self._run_attempts += 1
        if self._run_attempts > 1:
            raise APIConnectionError(
                "TechmoSynthesizeStream cannot be retried: the text input was already consumed by the previous "
                "attempt (sentence-level reconnection is handled internally)"
            )

        opts = replace(self._tts._opts)

        # Determine effective per-call deadline (same priority order as TechmoChunkedStream)
        if self._conn_options != DEFAULT_API_CONNECT_OPTIONS:
            timeout = self._conn_options.timeout
        elif opts.grpc_timeout is not None:
            timeout = opts.grpc_timeout
        else:
            timeout = _DEFAULT_GRPC_TIMEOUT

        # Markers from a failed framework-level attempt must not survive into a retry
        # of _run() on this same instance (see TechmoChunkedStream._run for details).
        self._markers.clear()

        # The emitter is initialized lazily on the first response so it can be stamped
        # with the sample rate the service actually delivers (SynthesizeResponse.
        # sampling_rate_hz) instead of the configured one. end_input()/join() require
        # an initialized emitter, so every exit path of _synthesize() must call
        # _ensure_initialized(). One segment for the entire stream — livekit-agents
        # 1.5.1 validates that SynthesizeStream produces exactly one segment per
        # push_text() call.
        initialized = False

        emitter_rate = 0

        def _ensure_initialized(response_rate: int = 0) -> None:
            nonlocal initialized, emitter_rate
            if not initialized:
                emitter_rate = response_rate or self._tts.sample_rate
                output_emitter.initialize(
                    request_id=shortuuid(),
                    sample_rate=emitter_rate,
                    num_channels=self._tts.num_channels,
                    mime_type="audio/pcm",
                    stream=True,
                )
                output_emitter.start_segment(segment_id=shortuuid())
                initialized = True
            elif response_rate and response_rate != emitter_rate:
                # The emitter's rate is fixed at initialization — PCM delivered at a
                # different rate would play at the wrong speed. Fail loudly instead.
                raise APIConnectionError(
                    f"Techmo TTS sample rate changed mid-stream: {emitter_rate} -> {response_rate} Hz"
                )

        sent_stream = self._tts._sentence_tokenizer.stream()

        async def _forward_input() -> None:
            async for data in self._input_ch:
                if isinstance(data, tts.SynthesizeStream._FlushSentinel):
                    sent_stream.flush()
                    continue
                sent_stream.push_text(data)
            sent_stream.end_input()

        async def _synthesize() -> None:
            # Cumulative seconds of audio pushed by completed sentences — shifts each
            # sentence's marker offsets (relative to its own request) into stream time.
            # Accumulated in SECONDS (not samples) so sentences delivered at different
            # sample rates, and odd-length byte chunks, stay accurate.
            stream_seconds = 0.0
            async for ev in sent_stream:
                text = ev.token.strip() if ev.token else ""
                if not text:
                    continue

                request = _build_request(text, opts)
                metadata = _build_metadata(opts)

                sentence_received = False
                sentence_seconds = 0.0
                for attempt in range(2):
                    channel: grpc.aio.Channel | None = None
                    try:
                        channel = await self._tts._get_channel()
                        stub = TTSStub(channel)
                        async for response in stub.SynthesizeStreaming(
                            request,
                            metadata=metadata,
                            timeout=timeout,
                        ):
                            sentence_received = True
                            _ensure_initialized(response.sampling_rate_hz)
                            # emitter_rate (not the configured fallback) resolves a
                            # mid-stream sampling_rate_hz=0: once established, the
                            # emitter rate is the single source of truth for timing.
                            # (A differing non-zero rate already raised in the guard.)
                            _emit_markers(response, emitter_rate, stream_seconds, self._markers, opts.on_marker)
                            if response.audio:
                                output_emitter.push(response.audio)
                                sentence_seconds += len(response.audio) / 2 / emitter_rate  # PCM16 mono
                        break
                    except grpc.aio.AioRpcError as e:
                        if e.code() == grpc.StatusCode.UNAVAILABLE:
                            # Invalidate the broken channel so the next acquire()
                            # (ours or a framework-level retry) gets a fresh one.
                            if channel is not None:
                                await self._tts._reset_channel(channel)
                            if attempt == 0 and not sentence_received:
                                logger.warning(
                                    "Techmo TTS channel UNAVAILABLE; reconnecting (attempt %d)...",
                                    attempt + 1,
                                )
                                continue
                            # A response for this sentence was already received — an
                            # internal retry would duplicate its audio or markers.
                            # Raise instead.
                            raise APIConnectionError(f"Techmo TTS gRPC error [{e.code()}]: {e.details()}") from e
                        if e.code() in (grpc.StatusCode.CANCELLED, grpc.StatusCode.OK):
                            # Graceful end by the service, but any not-yet-synthesized
                            # sentences are dropped — leave a trace of the truncation.
                            logger.warning(
                                "Techmo TTS streaming synthesis ended early by the service [%s] — "
                                "remaining sentences dropped: %s",
                                e.code(),
                                e.details(),
                            )
                            _ensure_initialized()
                            return
                        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                            raise APITimeoutError(f"Techmo TTS timed out: {e.details()}") from e
                        raise APIConnectionError(f"Techmo TTS gRPC error [{e.code()}]: {e.details()}") from e
                stream_seconds += sentence_seconds
            _ensure_initialized()
            output_emitter.flush()
            output_emitter.end_segment()

        tasks = [
            asyncio.create_task(_forward_input(), name="techmo-tts-forward"),
            asyncio.create_task(_synthesize(), name="techmo-tts-synth"),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            # Cancel tasks first so they stop using sent_stream before we close it.
            await cancel_and_wait(*tasks)
            await sent_stream.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_prosody(opts: TTSOptions) -> None:
    """Log warnings for prosody parameter values outside the expected service range."""
    for name, val, lo, hi in [
        ("pitch", opts.pitch, 0.1, 4.0),
        ("rate", opts.rate, 0.1, 4.0),
        ("speech_range", opts.speech_range, 0.0, 4.0),
        ("stress", opts.stress, 0.0, 4.0),
        ("volume", opts.volume, 0.0, 4.0),
    ]:
        if not (lo <= val <= hi):
            logger.warning(
                "prosody param %s=%.3g is outside expected range [%g, %g]",
                name,
                val,
                lo,
                hi,
            )


def _emit_markers(
    response: SynthesizeResponse,
    fallback_rate: int,
    base_time_s: float,
    sink: list[MarkerEvent],
    on_marker: Callable[[MarkerEvent], None] | None,
) -> None:
    """Convert ``SynthesizeResponse.markers`` (sample offsets) to :class:`MarkerEvent`.

    ``base_time_s`` shifts the marker into stream time when the response belongs to a
    later per-sentence request (``TechmoSynthesizeStream``); it is kept in seconds so
    sentences delivered at different sample rates stay accurate. Callback errors are
    logged, never raised — a broken consumer must not abort synthesis.
    """
    if not response.markers:
        return
    rate = response.sampling_rate_hz or fallback_rate
    for m in response.markers:
        ev = MarkerEvent(
            name=m.name,
            time_s=base_time_s + m.position / rate,
            position_samples=m.position,
        )
        sink.append(ev)
        if on_marker is not None:
            try:
                on_marker(ev)
            except Exception:
                logger.exception("Techmo TTS on_marker callback raised")


def _discover_sample_rate(opts: TTSOptions) -> int:
    """Call ListVoices synchronously to discover the service's native sample rate.

    Falls back to ``opts.fallback_sample_rate`` if the call fails.
    Intended to be called via ``asyncio.to_thread()`` from an async context.
    Note: a thread cannot be cancelled — cancelling the surrounding prewarm task
    (e.g. in aclose()) lets this call run to its 10 s deadline in the background;
    the ``finally`` below guarantees the channel is closed either way.
    """
    try:
        channel = _make_sync_channel(opts)
        stub = TTSStub(channel)
        try:
            response = stub.ListVoices(ListVoicesRequest(), timeout=10.0)
        finally:
            channel.close()
        rate: int = response.sampling_rate_hz
        if rate > 0:
            logger.debug("Discovered Techmo TTS sample rate: %d Hz", rate)
            return rate
        logger.warning(
            "ListVoices returned sampling_rate_hz=0, using fallback %d Hz",
            opts.fallback_sample_rate,
        )
    except Exception as exc:
        logger.warning(
            "Failed to discover sample rate from %s (%s), using fallback %d Hz",
            opts.service_address,
            exc,
            opts.fallback_sample_rate,
        )
    return opts.fallback_sample_rate


def _make_sync_channel(opts: TTSOptions) -> grpc.Channel:
    if opts.tls or opts.ca_cert or opts.client_cert:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=opts.ca_cert,
            private_key=opts.client_key,
            certificate_chain=opts.client_cert,
        )
        return grpc.secure_channel(opts.service_address, credentials)
    return grpc.insecure_channel(opts.service_address)


def _make_persistent_channel(opts: TTSOptions) -> grpc.aio.Channel:
    """Create a long-lived async gRPC channel to be reused across requests."""
    if opts.tls or opts.ca_cert or opts.client_cert:
        credentials = grpc.ssl_channel_credentials(
            root_certificates=opts.ca_cert,
            private_key=opts.client_key,
            certificate_chain=opts.client_cert,
        )
        return grpc.aio.secure_channel(opts.service_address, credentials)
    return grpc.aio.insecure_channel(opts.service_address)


def _build_request(text: str, opts: TTSOptions) -> SynthesizeRequest:
    voice = Voice(
        name=opts.voice_name,
        variant=opts.voice_variant,
    )

    prosody = ProsodicProperties(
        pitch=opts.pitch,
        range=opts.speech_range,
        rate=opts.rate,
        stress=opts.stress,
        volume=opts.volume,
    )

    synthesis_config = SynthesisConfig(
        language_code=opts.language_code,
        voice=voice,
        prosodic_properties=prosody,
    )

    output_config = OutputConfig(
        audio_encoding=AudioEncoding.Value("PCM16"),
        sampling_rate_hz=opts.output_sample_rate,  # 0 = service native; honoured since service 3.2.4
        max_frame_size=opts.max_frame_size,  # 0 = service default; honoured since service 3.2.4
    )

    return SynthesizeRequest(
        text=text,
        synthesis_config=synthesis_config,
        output_config=output_config,
    )


def _build_metadata(opts: TTSOptions) -> list[tuple[str, str]]:
    # Key is "session_id" (underscore) — matches the Techmo TTS service convention
    # used by tts-client-python. Do not change to "session-id" (hyphen).
    metadata = [("user-agent", f"livekit-plugins-techmo/{__version__}")]
    if opts.session_id:
        metadata.insert(0, ("session_id", opts.session_id))
    return metadata
