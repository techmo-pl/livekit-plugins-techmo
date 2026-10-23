"""Unit tests for the Techmo TTS plugin.

These tests mock the gRPC layer and do not require a running TTS service.

Tests marked with ``@requires_real_stubs`` need the real protobuf stubs
provided by the ``tts-api`` package (``tts_service_api.v3``).  After the
migration from local generated stubs, ``pip install -e .`` is sufficient.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from livekit.plugins.techmo.tts import TTSOptions

import pytest

# ---------------------------------------------------------------------------
# Skip marker for tests that need real proto stubs (tts-api package)
# ---------------------------------------------------------------------------


def _has_real_stubs() -> bool:
    try:
        import tts_service_api.v3 as _m  # noqa: F401

        return True
    except Exception:
        return False


requires_real_stubs = pytest.mark.skipif(
    not _has_real_stubs(),
    reason="requires tts-api package (pip install -e .)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_opts(service_address: str = "localhost:9999", **kwargs: Any) -> TTSOptions:
    """Return a TTSOptions with sane defaults for testing."""
    from livekit.plugins.techmo.tts import TTSOptions

    return TTSOptions(service_address=service_address, **kwargs)


class _FakeRpcError(Exception):
    """Minimal stand-in for grpc.aio.AioRpcError that can be raised in tests.

    grpc.aio.AioRpcError is a C-extension type whose __init__ requires real
    Metadata objects.  This class inherits from it (so isinstance checks pass)
    but bypasses the problematic __init__.
    """

    def __init__(self, status_code: Any, details: str = "error") -> None:

        Exception.__init__(self, details)
        self._status_code = status_code
        self._details = details
        # Make isinstance(e, grpc.aio.AioRpcError) True by patching __class__
        # is not possible without metaclass tricks; instead we inherit properly
        # but skip the super().__init__ call.

    def code(self) -> Any:
        return self._status_code

    def details(self) -> str:
        return self._details


# Ensure _FakeRpcError inherits from grpc.aio.AioRpcError so the except
# clause in _run() catches it.  We do this at import time after grpc loads.
def _make_rpc_error(status_code: Any, details: str = "error") -> _FakeRpcError:
    """Build a fake gRPC error that can be raised and caught correctly."""
    import grpc.aio

    # Dynamically create a subclass of AioRpcError that bypasses __init__
    FakeErr: type[_FakeRpcError] = type(
        "_DynFakeRpcError",
        (grpc.aio.AioRpcError,),
        {
            "__init__": _FakeRpcError.__init__,
            "code": _FakeRpcError.code,
            "details": _FakeRpcError.details,
        },
    )
    return FakeErr(status_code, details)


# ---------------------------------------------------------------------------
# TTSOptions
# ---------------------------------------------------------------------------


def test_tts_options_defaults() -> None:
    from livekit.plugins.techmo.tts import TTSOptions

    opts = TTSOptions(service_address="localhost:9999")
    assert opts.model == "standard"
    assert opts.voice_name is None  # service default voice
    assert opts.language_code is None  # service default language
    assert opts.pitch == 1.0
    assert opts.speech_range == 1.0
    assert opts.rate == 1.0
    assert opts.fallback_sample_rate == 22050
    assert opts.session_id is None  # not sent unless given


def test_tts_options_custom() -> None:
    from livekit.plugins.techmo.tts import TTSOptions

    opts = TTSOptions(
        service_address="tts.example.com:9999",
        model="enterprise",
        voice_name="ewa",
        language_code="en-US",
        rate=1.2,
        speech_range=0.8,
        session_id="test-session",
    )
    assert opts.model == "enterprise"
    assert opts.voice_name == "ewa"
    assert opts.language_code == "en-US"
    assert opts.rate == 1.2
    assert opts.speech_range == 0.8
    assert opts.session_id == "test-session"


# ---------------------------------------------------------------------------
# _build_request  (require real proto stubs — mocks can't store field values)
# ---------------------------------------------------------------------------


@requires_real_stubs
def test_build_request_voice_name() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", voice_name="ewa", voice_variant=2)
    req = _build_request("hello", opts)
    assert req.text == "hello"
    assert req.synthesis_config.voice.name == "ewa"
    assert req.synthesis_config.voice.variant == 2


@requires_real_stubs
def test_build_request_prosody() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", rate=1.5, pitch=0.9, speech_range=1.1)
    req = _build_request("test", opts)
    pp = req.synthesis_config.prosodic_properties
    assert pp.rate == pytest.approx(1.5)
    assert pp.pitch == pytest.approx(0.9)
    assert pp.range == pytest.approx(1.1)


@requires_real_stubs
def test_build_request_language_code() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", language_code="en-US")
    req = _build_request("hello", opts)
    assert req.synthesis_config.language_code == "en-US"


@requires_real_stubs
def test_build_request_output_config_pcm() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999")
    req = _build_request("test", opts)
    # PCM16 = 0 in the AudioEncoding enum
    assert req.output_config.audio_encoding == 0


# ---------------------------------------------------------------------------
# _build_metadata
# ---------------------------------------------------------------------------


def test_build_metadata_session_id() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_metadata

    opts = TTSOptions(service_address="localhost:9999", session_id="my-session")
    metadata = _build_metadata(opts)
    assert ("session_id", "my-session") in metadata


@pytest.mark.parametrize("session_id", [None, ""])
def test_tts_without_session_id_sends_no_session_metadata(session_id: str | None) -> None:
    # like tts-client-python without --session-id: no header, the service assigns its own ID
    from livekit.plugins.techmo.tts import TTS, _build_metadata

    instance = TTS(service_address="localhost:9999", session_id=session_id)
    assert instance._opts.session_id is None
    assert [k for k, _ in _build_metadata(instance._opts)] == ["user-agent"]


def test_build_metadata_user_agent() -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _build_metadata
    from livekit.plugins.techmo.version import __version__

    opts = TTSOptions(service_address="localhost:9999")
    metadata = _build_metadata(opts)
    keys = [k for k, _ in metadata]
    assert "user-agent" in keys
    ua_val = next(v for k, v in metadata if k == "user-agent")
    assert __version__ in ua_val
    assert ua_val == f"livekit-plugins-techmo/{__version__}"


# ---------------------------------------------------------------------------
# _discover_sample_rate
# ---------------------------------------------------------------------------


def _make_list_voices_response(sampling_rate_hz: int) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.sampling_rate_hz = sampling_rate_hz
    return mock_resp


@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
def test_discover_sample_rate_success(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _discover_sample_rate

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel

    mock_stub = MagicMock()
    mock_stub.ListVoices.return_value = _make_list_voices_response(22050)
    mock_stub_cls.return_value = mock_stub

    opts = TTSOptions(service_address="localhost:9999")
    rate = _discover_sample_rate(opts)

    assert rate == 22050
    mock_channel.close.assert_called_once()


@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
def test_discover_sample_rate_fallback_on_grpc_error(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _discover_sample_rate

    mock_channel_fn.side_effect = Exception("connection refused")

    opts = TTSOptions(service_address="localhost:9999", fallback_sample_rate=16000)
    rate = _discover_sample_rate(opts)

    assert rate == 16000


@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
def test_discover_sample_rate_fallback_on_zero(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    from livekit.plugins.techmo.tts import TTSOptions, _discover_sample_rate

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel

    mock_stub = MagicMock()
    mock_stub.ListVoices.return_value = _make_list_voices_response(0)
    mock_stub_cls.return_value = mock_stub

    opts = TTSOptions(service_address="localhost:9999", fallback_sample_rate=22050)
    rate = _discover_sample_rate(opts)

    assert rate == 22050


# ---------------------------------------------------------------------------
# TTS class construction
# ---------------------------------------------------------------------------


def test_tts_constructor_uses_fallback_sample_rate() -> None:
    """__init__ must not make a network call; sample_rate == fallback_sample_rate."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=16000)
    assert t.sample_rate == 16000
    assert t.num_channels == 1
    assert t.capabilities.streaming is True


def test_tts_constructor_default_sample_rate() -> None:
    from livekit.plugins.techmo.tts import _FALLBACK_SAMPLE_RATE, TTS

    t = TTS(service_address="localhost:9999")
    assert t.sample_rate == _FALLBACK_SAMPLE_RATE


def test_tts_constructor_raises_without_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TECHMO_TTS_ADDRESS", raising=False)
    from livekit.plugins.techmo.tts import TTS

    with pytest.raises(ValueError, match="service_address"):
        TTS()


def test_tts_constructor_reads_env_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_ADDRESS", "env.host:9999")
    t = TTS()
    assert t._opts.service_address == "env.host:9999"


def test_tts_constructor_reads_env_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_VOICE", "ewa")
    t = TTS(service_address="localhost:9999")
    assert t._opts.voice_name == "ewa"


def test_tts_constructor_reads_env_language(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_LANGUAGE", "en-US")
    t = TTS(service_address="localhost:9999")
    assert t._opts.language_code == "en-US"


def test_tts_constructor_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit constructor args must take precedence over environment variables."""
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_VOICE", "ewa")
    monkeypatch.setenv("TECHMO_TTS_LANGUAGE", "en-US")
    t = TTS(service_address="localhost:9999", voice_name="michal", language_code="pl-PL")
    assert t._opts.voice_name == "michal"
    assert t._opts.language_code == "pl-PL"


def test_tts_provider() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    assert t.provider == "techmo"


def test_tts_model_default() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    assert t.model == "standard"


def test_tts_model_custom() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", model="enterprise")
    assert t.model == "enterprise"


def test_tts_voice_name_stored_separately() -> None:
    """voice_name is stored in opts — distinct from the model property."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", voice_name="ewa", model="standard")
    assert t._opts.voice_name == "ewa"
    assert t.model == "standard"


def test_tts_tls_warning_with_ca_cert(caplog: pytest.LogCaptureFixture) -> None:
    """Providing ca_cert without tls=True should log a warning."""
    import logging

    from livekit.plugins.techmo.tts import TTS

    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        TTS(service_address="localhost:9999", ca_cert=b"fake-cert")
    assert any("tls=False" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# T17: TLS credential file loading
# ---------------------------------------------------------------------------


def test_tts_ca_cert_file_is_read(tmp_path: pathlib.Path) -> None:
    """T17: ca_cert_file path must be opened and its bytes stored as ca_cert."""
    from livekit.plugins.techmo.tts import TTS

    cert_path = tmp_path / "ca.pem"
    cert_path.write_bytes(b"fake-ca-cert-bytes")

    t = TTS(service_address="localhost:9999", tls=True, ca_cert_file=str(cert_path))
    assert t._opts.ca_cert == b"fake-ca-cert-bytes", "ca_cert must be populated from ca_cert_file"


def test_tts_client_cert_and_key_files_are_read(tmp_path: pathlib.Path) -> None:
    """T17: client_cert_file and client_key_file paths must be read into the corresponding bytes fields."""
    from livekit.plugins.techmo.tts import TTS

    cert_path = tmp_path / "client.crt"
    key_path = tmp_path / "client.key"
    cert_path.write_bytes(b"client-cert-bytes")
    key_path.write_bytes(b"client-key-bytes")

    t = TTS(service_address="localhost:9999", tls=True, client_cert_file=str(cert_path), client_key_file=str(key_path))
    assert t._opts.client_cert == b"client-cert-bytes", "client_cert must be populated from client_cert_file"
    assert t._opts.client_key == b"client-key-bytes", "client_key must be populated from client_key_file"


def test_tts_bytes_arg_takes_precedence_over_file(tmp_path: pathlib.Path) -> None:
    """T17: If both ca_cert (bytes) and ca_cert_file are provided, the bytes arg wins."""
    from livekit.plugins.techmo.tts import TTS

    cert_path = tmp_path / "ca.pem"
    cert_path.write_bytes(b"file-cert-bytes")

    t = TTS(service_address="localhost:9999", tls=True, ca_cert=b"direct-bytes", ca_cert_file=str(cert_path))
    assert t._opts.ca_cert == b"direct-bytes", "bytes arg must take precedence over file path"


# ---------------------------------------------------------------------------
# TTS.prewarm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._discover_sample_rate", return_value=22050)
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_creates_channel(mock_make_channel: MagicMock, mock_discover: MagicMock) -> None:
    import asyncio

    from livekit.plugins.techmo.tts import TTS

    mock_ch = MagicMock()
    mock_make_channel.return_value = mock_ch

    created_tasks: list[asyncio.Task[None]] = []
    original_create_task = asyncio.create_task

    def _capture_create_task(coro, **kwargs):  # type: ignore[no-untyped-def]
        task = original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    t = TTS(service_address="localhost:9999")
    assert t._pool._entries == []  # pool empty before prewarm
    t.prewarm()
    await t._prewarm_done.wait()
    assert len(t._pool._entries) == 1
    assert t._pool._entries[0].channel is mock_ch


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._discover_sample_rate", return_value=16000)
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_warns_on_rate_mismatch(
    mock_make_channel: MagicMock, mock_discover: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """prewarm() should warn when the discovered rate differs from fallback_sample_rate."""
    import asyncio
    import logging

    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = MagicMock()

    created_tasks: list[asyncio.Task[None]] = []
    original_create_task = asyncio.create_task

    def _capture_create_task(coro, **kwargs):  # type: ignore[no-untyped-def]
        task = original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)
        t.prewarm()
        await t._prewarm_done.wait()

    assert any("16000" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _validate_prosody
# ---------------------------------------------------------------------------


def test_validate_prosody_no_warning_for_valid() -> None:
    """_validate_prosody should not warn for in-range values."""

    from livekit.plugins.techmo.tts import TTSOptions, _validate_prosody

    opts = TTSOptions(service_address="localhost:9999", pitch=1.0, rate=1.0, stress=1.0)
    # No warning expected — just ensure it doesn't raise
    _validate_prosody(opts)


def test_validate_prosody_warns_for_out_of_range(caplog: pytest.LogCaptureFixture) -> None:
    """_validate_prosody should warn when a prosody param is out of expected range."""
    import logging

    from livekit.plugins.techmo.tts import TTSOptions, _validate_prosody

    opts = TTSOptions(service_address="localhost:9999", pitch=10.0)
    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        _validate_prosody(opts)

    assert any("pitch" in rec.message for rec in caplog.records)


def test_tts_constructor_validates_prosody(caplog: pytest.LogCaptureFixture) -> None:
    """TTS.__init__ should call _validate_prosody and log for out-of-range values."""
    import logging

    from livekit.plugins.techmo.tts import TTS

    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        TTS(service_address="localhost:9999", rate=99.0)

    assert any("rate" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# TTS.update_options — NOT_GIVEN sentinel
# ---------------------------------------------------------------------------


def test_update_options_sets_fields() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    t.update_options(voice_name="ewa", rate=1.5)
    assert t._opts.voice_name == "ewa"
    assert t._opts.rate == pytest.approx(1.5)


def test_update_options_model() -> None:
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    t.update_options(model="enterprise")
    assert t._opts.model == "enterprise"
    assert t.model == "enterprise"


def test_update_options_grpc_timeout_can_be_cleared() -> None:
    """update_options(grpc_timeout=None) must clear a previously set timeout."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", grpc_timeout=30.0)
    assert t._opts.grpc_timeout == pytest.approx(30.0)

    # Explicitly clear it back to None
    t.update_options(grpc_timeout=None)
    assert t._opts.grpc_timeout is None


def test_update_options_omitted_grpc_timeout_unchanged() -> None:
    """Omitting grpc_timeout from update_options must leave the existing value."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", grpc_timeout=30.0)
    t.update_options(voice_name="ewa")  # grpc_timeout not provided
    assert t._opts.grpc_timeout == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# TTS._get_or_create_channel — persistent channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_get_channel_creates_once(mock_make_channel: MagicMock) -> None:
    """_get_channel() must create the channel only on first call (pool_size=1)."""
    from livekit.plugins.techmo.tts import TTS

    mock_ch = MagicMock()
    mock_make_channel.return_value = mock_ch

    t = TTS(service_address="localhost:9999")
    assert t._pool._entries == []  # not created yet

    ch1 = await t._get_channel()
    ch2 = await t._get_channel()

    assert ch1 is mock_ch
    assert ch2 is mock_ch
    # pool_size=1: channel created once, then reused (unless expired)
    mock_make_channel.assert_called_once()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_aclose_closes_channel(mock_make_channel: MagicMock) -> None:
    """aclose() must close all channels in the pool."""
    from livekit.plugins.techmo.tts import TTS

    mock_ch = AsyncMock()
    mock_make_channel.return_value = mock_ch

    t = TTS(service_address="localhost:9999")
    await t._get_channel()  # populate the pool
    assert len(t._pool._entries) == 1

    await t.aclose()

    mock_ch.close.assert_called_once()
    assert t._pool._entries == []


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_aclose_closes_in_flight_streams(mock_make_channel: MagicMock) -> None:
    """aclose() must call aclose() on all tracked in-flight streams."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    mock_make_channel.return_value = AsyncMock()

    t = TTS(service_address="localhost:9999")
    stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
    t._streams.add(stream)

    with patch.object(stream, "aclose", new_callable=AsyncMock) as mock_aclose:
        await t.aclose()
        mock_aclose.assert_called_once()


# ---------------------------------------------------------------------------
# TechmoChunkedStream._run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunked_stream_pushes_audio() -> None:
    """ChunkedStream._run() should push audio bytes from gRPC responses."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    response1 = MagicMock()
    response1.audio = b"\x00\x01" * 100
    response1.sampling_rate_hz = 22050
    response2 = MagicMock()
    response2.audio = b"\x02\x03" * 100
    response2.sampling_rate_hz = 22050

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response1
        yield response2

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="Witaj świecie",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )

        emitter = MagicMock()
        emitter.initialize = MagicMock()
        emitter.push = MagicMock()
        emitter.flush = MagicMock()

        await stream._run(emitter)

    emitter.initialize.assert_called_once()
    assert emitter.push.call_count == 2
    emitter.flush.assert_called_once()


@pytest.mark.asyncio
async def test_chunked_stream_skips_empty_audio() -> None:
    """ChunkedStream._run() should not push empty audio bytes."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    response_empty = MagicMock()
    response_empty.audio = b""
    response_empty.sampling_rate_hz = 22050
    response_real = MagicMock()
    response_real.audio = b"\x00\x01" * 50
    response_real.sampling_rate_hz = 22050

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response_empty
        yield response_real

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="test",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        emitter = MagicMock()
        await stream._run(emitter)

    assert emitter.push.call_count == 1


@pytest.mark.asyncio
async def test_chunked_stream_wraps_grpc_error() -> None:
    """ChunkedStream._run() should raise APIConnectionError on gRPC failure."""
    import grpc
    from livekit.agents import APIConnectionError
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    rpc_error = _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "connection refused")

    call_count = 0

    async def _fail_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        nonlocal call_count
        call_count += 1
        raise rpc_error
        yield  # make it an async generator

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fail_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="test",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        emitter = MagicMock()

        with pytest.raises(APIConnectionError):
            await stream._run(emitter)

    # UNAVAILABLE triggers one reconnect retry — so stub is called twice
    assert call_count == 2


@pytest.mark.asyncio
async def test_chunked_stream_cancelled_returns_silently() -> None:
    """CANCELLED status should exit silently — session ended gracefully."""
    import grpc
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    rpc_error = _make_rpc_error(grpc.StatusCode.CANCELLED, "session ended")

    async def _cancelled_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        raise rpc_error
        yield

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _cancelled_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="test",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        emitter = MagicMock()

        # Should NOT raise — CANCELLED is a graceful session end
        await stream._run(emitter)
        emitter.flush.assert_not_called()
        # The emitter must still be initialized — the framework calls end_input()/join()
        # after _run() returns and both raise on an uninitialized emitter.
        emitter.initialize.assert_called_once()


# ---------------------------------------------------------------------------
# Options snapshot — concurrent update_options() safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunked_stream_snapshots_opts() -> None:
    """TechmoChunkedStream must snapshot opts at construction, not at _run() time."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999", voice_name="michal")
    stream = TechmoChunkedStream(
        tts=t,
        input_text="test",
        conn_options=DEFAULT_API_CONNECT_OPTIONS,
    )
    # Snapshot taken — stream._opts is a copy
    assert stream._opts.voice_name == "michal"
    assert stream._opts is not t._opts

    # Mutate after construction — must NOT affect the in-flight stream
    t.update_options(voice_name="ewa")
    assert t._opts.voice_name == "ewa"
    assert stream._opts.voice_name == "michal"  # snapshot unchanged


# ---------------------------------------------------------------------------
# Timeout — conn_options vs grpc_timeout vs default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_passes_grpc_timeout_to_stub() -> None:
    """_run() must forward opts.grpc_timeout to SynthesizeStreaming when conn_options is default."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999", grpc_timeout=60.0)

    captured_kwargs: dict[str, Any] = {}

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        captured_kwargs.update(kwargs)
        return
        yield  # pragma: no cover

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        await stream._run(emitter)

    assert captured_kwargs.get("timeout") == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_run_uses_default_timeout_when_both_unset() -> None:
    """When grpc_timeout=None and conn_options is default, _run() must use _DEFAULT_GRPC_TIMEOUT."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import _DEFAULT_GRPC_TIMEOUT, TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")  # grpc_timeout=None

    captured_kwargs: dict[str, Any] = {}

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        captured_kwargs.update(kwargs)
        return
        yield  # pragma: no cover

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        await stream._run(emitter)

    assert captured_kwargs.get("timeout") == pytest.approx(_DEFAULT_GRPC_TIMEOUT)


@pytest.mark.asyncio
async def test_run_uses_conn_options_timeout_when_custom() -> None:
    """_run() must use conn_options.timeout when non-default conn_options is provided."""
    from livekit.agents.types import APIConnectOptions

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999", grpc_timeout=60.0)

    captured_kwargs: dict[str, Any] = {}

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        captured_kwargs.update(kwargs)
        return
        yield  # pragma: no cover

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    custom_conn_options = APIConnectOptions(timeout=5.0)

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=custom_conn_options)
        emitter = MagicMock()
        await stream._run(emitter)

    # conn_options.timeout=5.0 takes precedence over grpc_timeout=60.0
    assert captured_kwargs.get("timeout") == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Emitter sample rate — per-response SynthesizeResponse.sampling_rate_hz
# ---------------------------------------------------------------------------


async def _run_chunked_with_single_response(t: Any, sampling_rate_hz: int) -> MagicMock:
    """Drive ChunkedStream._run() with one fake response carrying the given rate."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TechmoChunkedStream

    response = MagicMock()
    response.audio = b"\x00\x01" * 100
    response.sampling_rate_hz = sampling_rate_hz

    async def _fake_synthesize_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_synthesize_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="test",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        emitter = MagicMock()
        await stream._run(emitter)
    return emitter


@pytest.mark.asyncio
async def test_run_emitter_uses_response_sample_rate() -> None:
    """The emitter must be stamped with the rate the service actually reports,
    not the constructor-time rate."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)
    emitter = await _run_chunked_with_single_response(t, sampling_rate_hz=16000)

    emitter.initialize.assert_called_once()
    assert emitter.initialize.call_args.kwargs["sample_rate"] == 16000


@pytest.mark.asyncio
async def test_run_emitter_falls_back_when_response_rate_zero() -> None:
    """Older services report sampling_rate_hz=0 — fall back to the configured rate."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)
    emitter = await _run_chunked_with_single_response(t, sampling_rate_hz=0)

    emitter.initialize.assert_called_once()
    assert emitter.initialize.call_args.kwargs["sample_rate"] == 22050


def test_tts_output_sample_rate_sets_pipeline_rate() -> None:
    """A non-zero output_sample_rate becomes the plugin's sample_rate."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", output_sample_rate=16000)
    assert t.sample_rate == 16000

    # Default: fallback_sample_rate still wins when no output rate is requested
    t2 = TTS(service_address="localhost:9999", fallback_sample_rate=24000)
    assert t2.sample_rate == 24000


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._discover_sample_rate", return_value=22050)
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_skips_discovery_when_output_rate_set(
    mock_make_channel: MagicMock, mock_discover: MagicMock
) -> None:
    """With an explicit output_sample_rate the native-rate check is meaningless — skip it."""
    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = MagicMock()

    t = TTS(service_address="localhost:9999", output_sample_rate=16000)
    t.prewarm()
    assert t._prewarm_done is not None
    await t._prewarm_done.wait()

    assert len(t._pool._entries) == 1  # channel still warmed up
    mock_discover.assert_not_called()


@pytest.mark.asyncio
async def test_chunked_stream_no_internal_retry_after_partial_audio() -> None:
    """UNAVAILABLE after a response was received must raise immediately — an internal
    retry would double-initialize the emitter and duplicate audio. The framework
    retries with a fresh emitter instead."""
    import grpc
    from livekit.agents import APIConnectionError
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    rpc_error = _make_rpc_error(grpc.StatusCode.UNAVAILABLE, "server restarted mid-stream")

    call_count = 0
    response = MagicMock()
    response.audio = b"\x00\x01" * 100
    response.sampling_rate_hz = 22050

    async def _fail_after_audio(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        nonlocal call_count
        call_count += 1
        yield response
        raise rpc_error

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fail_after_audio
    mock_channel = MagicMock()

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=mock_channel)),
        patch.object(t, "_reset_channel", new_callable=AsyncMock) as mock_reset,
    ):
        stream = TechmoChunkedStream(
            tts=t,
            input_text="test",
            conn_options=DEFAULT_API_CONNECT_OPTIONS,
        )
        emitter = MagicMock()

        with pytest.raises(APIConnectionError):
            await stream._run(emitter)

    assert call_count == 1  # no internal retry once a response was received
    assert emitter.push.call_count == 1  # no duplicated audio
    mock_reset.assert_awaited_once_with(mock_channel)  # broken channel still invalidated


# ---------------------------------------------------------------------------
# TTS.list_voices
# ---------------------------------------------------------------------------


def _make_voice_mock(name: str, supported_languages: list[str], variants_count: int = 1) -> MagicMock:
    """Mimic techmo.tts.api.v3.VoiceInfo — real proto field names."""
    v = MagicMock()
    v.name = name
    v.supported_languages = supported_languages
    v.variants_count = variants_count
    return v


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
async def test_list_voices_returns_voice_info(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    """list_voices() must parse the response and return a list of VoiceInfo objects."""
    from livekit.plugins.techmo.tts import TTS, VoiceInfo

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel

    mock_response = MagicMock()
    mock_response.voices = [
        _make_voice_mock("michal", ["pl-PL"], 2),
        _make_voice_mock("ewa", ["pl-PL", "en-US"], 1),
    ]

    mock_stub = MagicMock()
    mock_stub.ListVoices.return_value = mock_response
    mock_stub_cls.return_value = mock_stub

    t = TTS(service_address="localhost:9999")
    voices = await t.list_voices()

    assert len(voices) == 2
    assert isinstance(voices[0], VoiceInfo)
    assert voices[0].name == "michal"
    assert voices[0].language_codes == ["pl-PL"]
    assert voices[0].variants == 2
    assert voices[1].name == "ewa"
    assert voices[1].language_codes == ["pl-PL", "en-US"]
    assert voices[1].variants == 1
    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
async def test_list_voices_raises_api_connection_error_on_failure(
    mock_channel_fn: MagicMock, mock_stub_cls: MagicMock
) -> None:
    """list_voices() must raise APIConnectionError when the gRPC call fails."""
    from livekit.agents import APIConnectionError

    from livekit.plugins.techmo.tts import TTS

    mock_channel_fn.side_effect = Exception("connection refused")

    t = TTS(service_address="localhost:9999")

    with pytest.raises(APIConnectionError, match="list_voices failed"):
        await t.list_voices()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
async def test_list_voices_channel_always_closed(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    """list_voices() must close the sync channel even when ListVoices raises."""
    from livekit.agents import APIConnectionError

    from livekit.plugins.techmo.tts import TTS

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel

    mock_stub = MagicMock()
    mock_stub.ListVoices.side_effect = RuntimeError("rpc failed")
    mock_stub_cls.return_value = mock_stub

    t = TTS(service_address="localhost:9999")

    with pytest.raises(APIConnectionError):
        await t.list_voices()

    mock_channel.close.assert_called_once()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
async def test_list_voices_empty_when_no_voices_field(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    """list_voices() must return [] when the response has no .voices attribute."""
    from livekit.plugins.techmo.tts import TTS

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel

    # Response object with no .voices attribute
    mock_response = MagicMock(spec=[])  # spec=[] means no attributes allowed
    mock_stub = MagicMock()
    mock_stub.ListVoices.return_value = mock_response
    mock_stub_cls.return_value = mock_stub

    t = TTS(service_address="localhost:9999")
    voices = await t.list_voices()

    assert voices == []


# ---------------------------------------------------------------------------
# TTS._prewarm_done event (GAP-TTS-4)
# ---------------------------------------------------------------------------


@requires_real_stubs
@pytest.mark.asyncio
async def test_tts_has_prewarm_done_event() -> None:
    """TTS must have a _prewarm_done event set after prewarm() completes."""
    import asyncio

    from livekit.plugins.techmo.tts import TTS

    tts = TTS(service_address="localhost:9999")
    tts.prewarm()
    # _prewarm_done must exist immediately after prewarm() is called
    assert hasattr(tts, "_prewarm_done"), "TTS must have _prewarm_done after prewarm()"
    # Wait deterministically for the background task to finish (it will fail since
    # localhost:9999 is not available, but must still set the event on failure).
    await asyncio.wait_for(tts._prewarm_done.wait(), timeout=5.0)
    assert tts._prewarm_done.is_set(), "_prewarm_done must be set even on prewarm failure"
    await tts.aclose()


# ---------------------------------------------------------------------------
# TechmoChunkedStream.cancel() (GAP-TTS-3)
# ---------------------------------------------------------------------------


@requires_real_stubs
@pytest.mark.asyncio
async def test_techmo_chunked_stream_has_cancel() -> None:
    """TechmoChunkedStream must expose a public cancel() coroutine."""
    from livekit.plugins.techmo.tts import TTS

    tts = TTS(service_address="localhost:9999")
    stream = tts.synthesize("hello world")
    assert hasattr(stream, "cancel"), "TechmoChunkedStream must have cancel()"
    assert callable(stream.cancel)
    await stream.aclose()


# ---------------------------------------------------------------------------
# TechmoSynthesizeStream — native streaming (GAP-TTS-1)
# ---------------------------------------------------------------------------


@requires_real_stubs
@pytest.mark.asyncio
async def test_tts_stream_returns_native_synthesize_stream() -> None:
    """TTS.stream() must return a TechmoSynthesizeStream, not a StreamAdapterWrapper."""
    from livekit.plugins.techmo.tts import TTS, TechmoSynthesizeStream

    tts_obj = TTS(service_address="localhost:9999")
    stream = tts_obj.stream()
    assert isinstance(stream, TechmoSynthesizeStream), f"Expected TechmoSynthesizeStream, got {type(stream).__name__}"
    await stream.aclose()


@requires_real_stubs
def test_tts_has_no_stream_adapter() -> None:
    """TTS must not have an _adapter attribute (StreamAdapter removed)."""
    from livekit.plugins.techmo.tts import TTS

    tts = TTS(service_address="localhost:9999")
    assert not hasattr(tts, "_adapter"), "TTS must not hold a StreamAdapter — native streaming replaced it"


@requires_real_stubs
def test_tts_has_sentence_tokenizer() -> None:
    """TTS must expose _sentence_tokenizer for use by TechmoSynthesizeStream."""
    from livekit.plugins.techmo.tts import TTS

    tts = TTS(service_address="localhost:9999")
    assert hasattr(tts, "_sentence_tokenizer"), "TTS must have _sentence_tokenizer attribute"


@requires_real_stubs
def test_synthesize_response_has_no_timing_fields() -> None:
    """Document that SynthesizeResponse has no word-timing fields (KNOWN_ISSUES.md §4)."""
    from tts_service_api.v3 import SynthesizeResponse

    desc = SynthesizeResponse.DESCRIPTOR
    timing_fields = [
        f.name
        for f in desc.fields
        if any(kw in f.name for kw in ("timestamp", "timing", "alignment", "word_time", "time"))
    ]
    assert timing_fields == [], (
        f"SynthesizeResponse now has timing fields: {timing_fields}. "
        "Implement word-timing in TechmoSynthesizeStream._run()._synthesize() and remove this test."
    )


def test_prewarm_uses_create_task_not_ensure_future() -> None:
    """prewarm() must use asyncio.create_task, not the deprecated ensure_future."""
    import inspect

    try:
        from livekit.plugins.techmo.tts import TTS
    except ImportError:
        pytest.skip("gRPC stubs not generated")

    src = inspect.getsource(TTS.prewarm)
    assert "create_task" in src, "prewarm must use asyncio.create_task"
    assert "ensure_future" not in src, "prewarm must not use deprecated asyncio.ensure_future"


# ---------------------------------------------------------------------------
# T7–T9: _ChannelPool internals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_channel_pool_round_robin(mock_make_channel: MagicMock) -> None:
    """T7: _ChannelPool with pool_size=2 must round-robin across channels."""

    from livekit.plugins.techmo.tts import TTSOptions, _ChannelPool

    ch1 = MagicMock()
    ch2 = MagicMock()
    # The pool fills all slots on first acquire (while len < pool_size loop)
    mock_make_channel.side_effect = [ch1, ch2]

    opts = TTSOptions(service_address="localhost:9999", pool_size=2, session_lifetime_s=300.0)
    pool = _ChannelPool(opts)

    a = await pool.acquire()  # fills pool → ch1 at idx=0, ch2 at idx=1; returns ch1 (idx 0→1)
    b = await pool.acquire()  # pool full; returns ch2 (idx 1→2)
    c = await pool.acquire()  # pool full; idx 2%2=0 → ch1 (idx 2→3)

    assert a is ch1
    assert b is ch2
    assert c is ch1, "Round-robin must wrap around to ch1 on third acquire"
    assert mock_make_channel.call_count == 2, "Pool of size 2 must create exactly 2 channels"


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_channel_pool_expired_rotation(mock_make_channel: MagicMock) -> None:
    """T8: _ChannelPool must replace an expired entry and close the old channel on acquire()."""
    from livekit.plugins.techmo.tts import TTSOptions, _ChannelPool

    ch_original = AsyncMock()
    ch_replacement = MagicMock()
    mock_make_channel.side_effect = [ch_original, ch_replacement]

    opts = TTSOptions(service_address="localhost:9999", pool_size=1, session_lifetime_s=1.0)
    pool = _ChannelPool(opts)

    first = await pool.acquire()
    assert first is ch_original

    # Simulate expiry by moving created_at back past the lifetime threshold
    pool._entries[0].created_at -= 2.0

    second = await pool.acquire()

    assert second is ch_replacement, "Expired channel must be replaced"
    ch_original.close.assert_called_once()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_channel_pool_invalidate_replaces_channel(mock_make_channel: MagicMock) -> None:
    """T9: invalidate() must close the given channel and create a fresh replacement."""
    from livekit.plugins.techmo.tts import TTSOptions, _ChannelPool

    ch_old = AsyncMock()
    ch_new = MagicMock()
    mock_make_channel.side_effect = [ch_old, ch_new]

    opts = TTSOptions(service_address="localhost:9999", pool_size=1, session_lifetime_s=300.0)
    pool = _ChannelPool(opts)

    old_ch = await pool.acquire()
    assert old_ch is ch_old

    await pool.invalidate(old_ch)

    ch_old.close.assert_called_once()
    assert pool._entries[0].channel is ch_new, "Pool entry must hold the replacement channel after invalidate()"

    # Next acquire must return the new channel
    next_ch = await pool.acquire()
    assert next_ch is ch_new


# ---------------------------------------------------------------------------
# T1–T6, T11: TechmoSynthesizeStream behavioral tests
# ---------------------------------------------------------------------------


class _StaticInputChannel:
    """Async iterator yielding pre-defined items — substitutes SynthesizeStream._input_ch."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)
        self._idx = 0

    def __aiter__(self) -> _StaticInputChannel:
        return self

    async def __anext__(self) -> Any:
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


async def _make_synth_stream_with_input(
    t: Any,
    input_items: list[Any],
) -> tuple[Any, MagicMock]:
    """Return (stream, emitter) with _input_ch replaced and background task cancelled."""
    import asyncio

    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TechmoSynthesizeStream

    stream = TechmoSynthesizeStream(tts=t, conn_options=DEFAULT_API_CONNECT_OPTIONS)
    stream._task.cancel()
    await asyncio.gather(stream._task, return_exceptions=True)

    stream._input_ch = _StaticInputChannel(input_items)  # type: ignore[assignment]

    emitter = MagicMock()
    emitter.initialize = MagicMock()
    emitter.start_segment = MagicMock()
    emitter.end_segment = MagicMock()
    emitter.push = MagicMock()
    emitter.flush = MagicMock()
    return stream, emitter


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_calls_stub_per_sentence() -> None:
    """T1: _synthesize must call SynthesizeStreaming once per sentence from the tokenizer.

    The sentence tokenizer is mocked to produce two known sentences so the test
    is independent of blingfire's chunking heuristics.  The behavioral assertion
    is that each sentence token drives exactly one gRPC call.
    """

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    stub_call_texts: list[str] = []

    async def _fake_streaming(request: Any, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        stub_call_texts.append(request.text)
        return
        yield  # make it a generator

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    # Mock tokenizer to produce two controlled sentences, regardless of how
    # push_text is called (blingfire buffers chunks differently in one push vs streaming).
    class _TwoSentenceStream:
        def __init__(self) -> None:
            self._sentences = ["Hello world.", "How are you?"]
            self._idx = 0

        def push_text(self, text: str) -> None:
            pass

        def end_input(self) -> None:
            pass

        def flush(self) -> None:
            pass

        def __aiter__(self) -> _TwoSentenceStream:
            return self

        async def __anext__(self) -> Any:
            if self._idx >= len(self._sentences):
                raise StopAsyncIteration
            ev = MagicMock()
            ev.token = self._sentences[self._idx]
            self._idx += 1
            return ev

        async def aclose(self) -> None:
            pass

    mock_tok = MagicMock()
    mock_tok.stream.return_value = _TwoSentenceStream()
    t._sentence_tokenizer = mock_tok  # type: ignore[assignment]

    stream, emitter = await _make_synth_stream_with_input(t, ["Hello world. How are you?"])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    assert len(stub_call_texts) == 2, (
        f"Expected 2 synthesis calls (one per sentence), got {len(stub_call_texts)}: {stub_call_texts}"
    )
    assert stub_call_texts[0] == "Hello world."
    assert stub_call_texts[1] == "How are you?"


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_flush_sentinel_calls_tokenizer_flush() -> None:
    """T2: FlushSentinel in _input_ch must call flush() on the sentence tokenizer stream."""

    from livekit.agents import tts as _tts_agents

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    flush_calls: list[bool] = []

    # Replace the sentence tokenizer with a mock that records flush() calls
    class _MockSentStream:
        def flush(self) -> None:
            flush_calls.append(True)

        def push_text(self, text: str) -> None:
            pass

        def end_input(self) -> None:
            pass

        def __aiter__(self) -> _MockSentStream:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            pass

    mock_tokenizer = MagicMock()
    mock_tokenizer.stream.return_value = _MockSentStream()
    t._sentence_tokenizer = mock_tokenizer  # type: ignore[assignment]

    flush_sentinel = _tts_agents.SynthesizeStream._FlushSentinel()
    stream, emitter = await _make_synth_stream_with_input(t, ["hello there", flush_sentinel])

    with patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())):
        await stream._run(emitter)

    assert len(flush_calls) == 1, f"Expected flush() called once for FlushSentinel, called {len(flush_calls)} times"


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_deadline_exceeded_raises_timeout_error() -> None:
    """T3+T11: DEADLINE_EXCEEDED in TechmoSynthesizeStream._synthesize must raise APITimeoutError."""

    import grpc
    from livekit.agents import APITimeoutError

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    rpc_error = _make_rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED, "synthesis timed out")

    async def _timeout_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        raise rpc_error
        yield

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _timeout_streaming

    stream, emitter = await _make_synth_stream_with_input(t, ["Hello world."])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        with pytest.raises(APITimeoutError):
            await stream._run(emitter)


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_single_segment() -> None:
    """T4: TechmoSynthesizeStream must open exactly one segment for the entire stream."""

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        return
        yield

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    # Multi-sentence input: should still use exactly one segment
    stream, emitter = await _make_synth_stream_with_input(t, ["First sentence. Second sentence. Third sentence."])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    emitter.start_segment.assert_called_once()
    emitter.end_segment.assert_called_once()


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_skips_empty_tokens() -> None:
    """T5: _synthesize must skip empty and whitespace-only tokens from the tokenizer."""

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    stub_call_count = 0

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        nonlocal stub_call_count
        stub_call_count += 1
        return
        yield

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    # Inject a tokenizer that yields empty + whitespace tokens plus one real token
    class _EmptyThenRealStream:
        def __init__(self) -> None:
            self._tokens = ["", "   ", "real sentence"]
            self._idx = 0

        def flush(self) -> None:
            pass

        def push_text(self, text: str) -> None:
            pass

        def end_input(self) -> None:
            pass

        def __aiter__(self) -> _EmptyThenRealStream:
            return self

        async def __anext__(self) -> Any:
            if self._idx >= len(self._tokens):
                raise StopAsyncIteration
            token_text = self._tokens[self._idx]
            self._idx += 1
            ev = MagicMock()
            ev.token = token_text
            return ev

        async def aclose(self) -> None:
            pass

    mock_tokenizer = MagicMock()
    mock_tokenizer.stream.return_value = _EmptyThenRealStream()
    t._sentence_tokenizer = mock_tokenizer  # type: ignore[assignment]

    stream, emitter = await _make_synth_stream_with_input(t, ["ignored — tokenizer is mocked"])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    assert stub_call_count == 1, f"Expected 1 synthesis call (skipping 2 empty tokens), got {stub_call_count}"


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_interleaved_push_and_flush() -> None:
    """T6: Interleaved push_text and FlushSentinel must produce synthesis for each segment."""

    from livekit.agents import tts as _tts_agents

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    stub_call_texts: list[str] = []

    async def _fake_streaming(request: Any, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        stub_call_texts.append(request.text)
        return
        yield

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    flush_sentinel = _tts_agents.SynthesizeStream._FlushSentinel()

    # Push two separate sentences with a flush in-between
    stream, emitter = await _make_synth_stream_with_input(t, ["Hello world.", flush_sentinel, "Goodbye world."])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    # Both sentences must be synthesized
    assert len(stub_call_texts) == 2, (
        f"Expected 2 synthesis calls for 2 sentences with flush, got {len(stub_call_texts)}: {stub_call_texts}"
    )
    assert any("Hello world" in t for t in stub_call_texts)
    assert any("Goodbye world" in t for t in stub_call_texts)


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_emitter_uses_response_sample_rate() -> None:
    """The streaming emitter must be stamped with the per-response rate (stream=True),
    with the single segment opened on the first response."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)

    response = MagicMock()
    response.audio = b"\x00\x01" * 100
    response.sampling_rate_hz = 16000

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    stream, emitter = await _make_synth_stream_with_input(t, ["Hello world."])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    emitter.initialize.assert_called_once()
    assert emitter.initialize.call_args.kwargs["sample_rate"] == 16000
    assert emitter.initialize.call_args.kwargs["stream"] is True
    emitter.start_segment.assert_called_once()
    emitter.end_segment.assert_called_once()


# ---------------------------------------------------------------------------
# T12/T13: output_sample_rate and max_frame_size in _build_request proto output
# ---------------------------------------------------------------------------


@requires_real_stubs
def test_build_request_output_sample_rate() -> None:
    """T12: output_sample_rate must be forwarded to OutputConfig.sampling_rate_hz."""
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", output_sample_rate=22050)
    req = _build_request("hello", opts)
    assert req.output_config.sampling_rate_hz == 22050


@requires_real_stubs
def test_build_request_output_sample_rate_zero_means_native() -> None:
    """T12: output_sample_rate=0 (default) must pass 0 to the proto (service native)."""
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999")  # output_sample_rate=0 default
    req = _build_request("hello", opts)
    assert req.output_config.sampling_rate_hz == 0


@requires_real_stubs
def test_build_request_max_frame_size() -> None:
    """T13: max_frame_size must be forwarded to OutputConfig.max_frame_size."""
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", max_frame_size=512)
    req = _build_request("hello", opts)
    assert req.output_config.max_frame_size == 512


@requires_real_stubs
def test_build_request_max_frame_size_zero_means_default() -> None:
    """T13: max_frame_size=0 (default) must pass 0 to the proto (service default)."""
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999")  # max_frame_size=0 default
    req = _build_request("hello", opts)
    assert req.output_config.max_frame_size == 0


# ---------------------------------------------------------------------------
# T14: stress and volume prosody fields in _build_request + _validate_prosody
# ---------------------------------------------------------------------------


@requires_real_stubs
def test_build_request_stress_and_volume() -> None:
    """T14: stress and volume must appear in ProsodicProperties of the synthesize request."""
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    opts = TTSOptions(service_address="localhost:9999", stress=2.0, volume=0.5)
    req = _build_request("hello", opts)
    pp = req.synthesis_config.prosodic_properties
    assert pp.stress == pytest.approx(2.0)
    assert pp.volume == pytest.approx(0.5)


def test_validate_prosody_warns_for_out_of_range_stress(caplog: pytest.LogCaptureFixture) -> None:
    """T14: _validate_prosody must warn when stress is outside [0.0, 4.0]."""
    import logging

    from livekit.plugins.techmo.tts import TTSOptions, _validate_prosody

    opts = TTSOptions(service_address="localhost:9999", stress=5.0)
    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        _validate_prosody(opts)

    assert any("stress" in rec.message for rec in caplog.records)


def test_validate_prosody_warns_for_out_of_range_volume(caplog: pytest.LogCaptureFixture) -> None:
    """T14: _validate_prosody must warn when volume is outside [0.0, 4.0]."""
    import logging

    from livekit.plugins.techmo.tts import TTSOptions, _validate_prosody

    opts = TTSOptions(service_address="localhost:9999", volume=10.0)
    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        _validate_prosody(opts)

    assert any("volume" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# T10: _ChannelPool.aclose() idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_channel_pool_aclose_idempotent(mock_make_channel: MagicMock) -> None:
    """T10: _ChannelPool.aclose() must be safe to call twice without raising."""
    from livekit.plugins.techmo.tts import TTSOptions, _ChannelPool

    mock_ch = AsyncMock()
    mock_make_channel.return_value = mock_ch

    opts = TTSOptions(service_address="localhost:9999", pool_size=1, session_lifetime_s=300.0)
    pool = _ChannelPool(opts)

    await pool.acquire()  # populate the pool
    await pool.aclose()  # first close — empties _entries
    await pool.aclose()  # second close — _entries already empty, must not raise

    assert pool._entries == []
    # Channel must have been closed exactly once (only during the first aclose)
    mock_ch.close.assert_called_once()


# ---------------------------------------------------------------------------
# T16: TTS.aclose() idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_tts_aclose_idempotent(mock_make_channel: MagicMock) -> None:
    """T16: TTS.aclose() must be safe to call twice without raising."""
    from livekit.plugins.techmo.tts import TTS

    mock_ch = AsyncMock()
    mock_make_channel.return_value = mock_ch

    t = TTS(service_address="localhost:9999")
    await t._get_channel()  # populate pool so there's something to close

    await t.aclose()  # first close
    await t.aclose()  # second close — must not raise


# ---------------------------------------------------------------------------
# prewarm() hardening — task reference, no-loop no-op, aclose() cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._discover_sample_rate", return_value=22050)
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_keeps_task_reference(mock_make_channel: MagicMock, mock_discover: MagicMock) -> None:
    """prewarm() must hold a strong reference to its task (GC safety)."""
    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = MagicMock()

    t = TTS(service_address="localhost:9999")
    t.prewarm()
    assert t._prewarm_task is not None
    assert t._prewarm_done is not None
    await t._prewarm_done.wait()
    await t._prewarm_task  # completed without error


def test_prewarm_without_event_loop_is_noop(caplog: pytest.LogCaptureFixture) -> None:
    """Outside a running loop prewarm() must warn and no-op — not raise RuntimeError."""
    import logging

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    with caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"):
        t.prewarm()  # sync context: no running event loop

    assert t._prewarm_task is None
    assert t._prewarm_done is not None
    assert t._prewarm_done.is_set()  # waiters must not hang
    assert any("without a running event loop" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_aclose_cancels_inflight_prewarm(mock_make_channel: MagicMock) -> None:
    """aclose() must cancel a still-running prewarm so it cannot recreate a channel
    after the pool has been torn down."""
    import asyncio

    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = AsyncMock()

    t = TTS(service_address="localhost:9999")

    never = asyncio.Event()

    async def _hang(done_event: asyncio.Event) -> None:
        try:
            await never.wait()
        finally:
            done_event.set()

    with patch.object(t, "_prewarm_async", new=_hang):
        t.prewarm()
        await asyncio.sleep(0)  # let the task start
        assert t._prewarm_task is not None and not t._prewarm_task.done()

        await t.aclose()
        await asyncio.gather(t._prewarm_task, return_exceptions=True)

    assert t._prewarm_task.cancelled()
    assert t._pool._entries == []  # nothing resurrected after aclose


# ---------------------------------------------------------------------------
# SSML markers — MarkerEvent collection and on_marker callback
# ---------------------------------------------------------------------------


def _make_marker_mock(position: int, name: str) -> MagicMock:
    m = MagicMock()
    m.position = position
    m.name = name
    return m


@pytest.mark.asyncio
async def test_chunked_stream_collects_markers() -> None:
    """Markers must be converted to seconds using the per-response rate and delivered
    both via stream.markers and the on_marker callback."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, MarkerEvent, TechmoChunkedStream

    received: list[MarkerEvent] = []
    t = TTS(service_address="localhost:9999", on_marker=received.append)

    response = MagicMock()
    response.audio = b"\x00\x01" * 22050
    response.sampling_rate_hz = 22050
    response.markers = [_make_marker_mock(11025, "m1"), _make_marker_mock(22050, "m2")]

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        await stream._run(emitter)

    assert [m.name for m in stream.markers] == ["m1", "m2"]
    assert stream.markers[0].time_s == pytest.approx(0.5)
    assert stream.markers[1].time_s == pytest.approx(1.0)
    assert stream.markers[0].position_samples == 11025
    assert received == stream.markers


@pytest.mark.asyncio
async def test_marker_callback_error_does_not_abort_synthesis() -> None:
    """An on_marker callback that raises must be logged, not propagated."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, MarkerEvent, TechmoChunkedStream

    def _boom(ev: MarkerEvent) -> None:
        raise ValueError("consumer bug")

    t = TTS(service_address="localhost:9999", on_marker=_boom)

    response = MagicMock()
    response.audio = b"\x00\x01" * 100
    response.sampling_rate_hz = 22050
    response.markers = [_make_marker_mock(50, "m1")]

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        await stream._run(emitter)  # must not raise

    assert len(stream.markers) == 1
    assert emitter.push.call_count == 1  # synthesis continued


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_markers_cumulative_offset() -> None:
    """In the streaming path, marker times must be cumulative across sentences:
    a marker in sentence 2 is offset by sentence 1's audio duration."""

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)

    # Each sentence returns 1.0 s of audio (22050 samples) with a marker at 0.5 s.
    call_idx = 0

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        nonlocal call_idx
        call_idx += 1
        response = MagicMock()
        response.audio = b"\x00\x01" * 22050  # 22050 PCM16 samples = 1.0 s
        response.sampling_rate_hz = 22050
        response.markers = [_make_marker_mock(11025, f"s{call_idx}")]
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    class _TwoSentenceStream:
        def __init__(self) -> None:
            self._sentences = ["First sentence.", "Second sentence."]
            self._idx = 0

        def push_text(self, text: str) -> None:
            pass

        def end_input(self) -> None:
            pass

        def flush(self) -> None:
            pass

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            if self._idx >= len(self._sentences):
                raise StopAsyncIteration
            ev = MagicMock()
            ev.token = self._sentences[self._idx]
            self._idx += 1
            return ev

        async def aclose(self) -> None:
            pass

    mock_tok = MagicMock()
    mock_tok.stream.return_value = _TwoSentenceStream()
    t._sentence_tokenizer = mock_tok  # type: ignore[assignment]

    stream, emitter = await _make_synth_stream_with_input(t, ["ignored — tokenizer is mocked"])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        await stream._run(emitter)

    assert [m.name for m in stream.markers] == ["s1", "s2"]
    assert stream.markers[0].time_s == pytest.approx(0.5)  # 11025 / 22050
    assert stream.markers[1].time_s == pytest.approx(1.5)  # 1.0 s of sentence 1 + 11025/22050


# ---------------------------------------------------------------------------
# Adversarial-review regressions (Codex round 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._discover_sample_rate", return_value=22050)
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_is_idempotent_while_in_flight(mock_make_channel: MagicMock, mock_discover: MagicMock) -> None:
    """A second prewarm() while the first is pending must not spawn/orphan a new task."""
    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = MagicMock()

    t = TTS(service_address="localhost:9999")
    t.prewarm()
    first_task = t._prewarm_task
    first_done = t._prewarm_done
    t.prewarm()  # in flight — must be a no-op
    assert t._prewarm_task is first_task
    assert t._prewarm_done is first_done
    assert first_done is not None
    await first_done.wait()


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_cancelled_before_start_still_sets_done(mock_make_channel: MagicMock) -> None:
    """Cancelling prewarm before its coroutine ever runs must still set _prewarm_done —
    a task cancelled while pending never executes the coroutine's finally block."""
    import asyncio

    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = AsyncMock()

    t = TTS(service_address="localhost:9999")
    t.prewarm()
    # No event-loop yield between prewarm() and aclose(): the task is still pending.
    await t.aclose()

    assert t._prewarm_done is not None
    await asyncio.wait_for(t._prewarm_done.wait(), timeout=1.0)  # must not hang


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_pool_acquire_after_aclose_raises(mock_make_channel: MagicMock) -> None:
    """After aclose() the pool is terminal — a late acquire must raise, not resurrect
    a channel."""
    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = AsyncMock()

    t = TTS(service_address="localhost:9999")
    await t._get_channel()
    await t.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await t._get_channel()
    assert t._pool._entries == []


@pytest.mark.asyncio
async def test_stream_instances_are_tracked_for_aclose() -> None:
    """stream() must register the native SynthesizeStream so aclose() can close it."""
    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")
    s = t.stream()
    try:
        assert s in t._streams
    finally:
        await s.aclose()


def test_constructor_rejects_invalid_sample_rates() -> None:
    """fallback_sample_rate <= 0 or negative output_sample_rate must fail fast —
    they feed emitter initialization and marker division."""
    from livekit.plugins.techmo.tts import TTS

    with pytest.raises(ValueError, match="fallback_sample_rate"):
        TTS(service_address="localhost:9999", fallback_sample_rate=0)
    with pytest.raises(ValueError, match="output_sample_rate"):
        TTS(service_address="localhost:9999", output_sample_rate=-1)


@pytest.mark.asyncio
async def test_framework_retry_does_not_duplicate_markers() -> None:
    """The framework retries _run() on the SAME stream instance (fresh emitter);
    markers from the failed attempt must not accumulate."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    response = MagicMock()
    response.audio = b"\x00\x01" * 100
    response.sampling_rate_hz = 22050
    response.markers = [_make_marker_mock(50, "m1")]

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        await stream._run(MagicMock())  # attempt 1 (as if it later failed)
        await stream._run(MagicMock())  # framework retry on the same instance

    assert len(stream.markers) == 1  # not 2 — cleared per attempt


@requires_real_stubs
@pytest.mark.asyncio
async def test_synthesize_stream_rejects_mid_stream_rate_change() -> None:
    """A response rate differing from the emitter's initialized rate must raise —
    pushing PCM at another rate would silently play at the wrong speed. Markers
    already delivered by earlier sentences are retained."""

    from livekit.agents import APIConnectionError

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)

    # Sentence 1: 22050 samples @ 22050 Hz = 1.0 s.
    # Sentence 2: reports 16000 Hz — must be rejected, not mis-timed.
    responses = [
        (22050, 22050, 11025, "s1"),
        (16000, 16000, 8000, "s2"),
    ]
    call_idx = 0

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        nonlocal call_idx
        rate, n_samples, mark_pos, mark_name = responses[call_idx]
        call_idx += 1
        response = MagicMock()
        response.audio = b"\x00\x01" * n_samples
        response.sampling_rate_hz = rate
        response.markers = [_make_marker_mock(mark_pos, mark_name)]
        yield response

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    class _TwoSentenceStream:
        def __init__(self) -> None:
            self._sentences = ["First sentence.", "Second sentence."]
            self._idx = 0

        def push_text(self, text: str) -> None:
            pass

        def end_input(self) -> None:
            pass

        def flush(self) -> None:
            pass

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> Any:
            if self._idx >= len(self._sentences):
                raise StopAsyncIteration
            ev = MagicMock()
            ev.token = self._sentences[self._idx]
            self._idx += 1
            return ev

        async def aclose(self) -> None:
            pass

    mock_tok = MagicMock()
    mock_tok.stream.return_value = _TwoSentenceStream()
    t._sentence_tokenizer = mock_tok  # type: ignore[assignment]

    stream, emitter = await _make_synth_stream_with_input(t, ["ignored — tokenizer is mocked"])

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        with pytest.raises(APIConnectionError, match="sample rate changed"):
            await stream._run(emitter)

    # Sentence 1's marker was delivered before the failure and is retained.
    assert [m.name for m in stream.markers] == ["s1"]
    assert stream.markers[0].time_s == pytest.approx(0.5)  # 11025 / 22050


@pytest.mark.asyncio
async def test_chunked_stream_rejects_mid_stream_rate_change() -> None:
    """Same guard in the chunked path: a second response at a different rate raises."""
    from livekit.agents import APIConnectionError
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    t = TTS(service_address="localhost:9999")

    r1 = MagicMock()
    r1.audio = b"\x00\x01" * 100
    r1.sampling_rate_hz = 22050
    r1.markers = []
    r2 = MagicMock()
    r2.audio = b"\x00\x01" * 100
    r2.sampling_rate_hz = 16000
    r2.markers = []

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield r1
        yield r2

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        with pytest.raises(APIConnectionError, match="sample rate changed"):
            await stream._run(emitter)


@pytest.mark.asyncio
async def test_synthesize_stream_second_run_fails_fast() -> None:
    """A framework-level retry of _run() cannot replay the drained input channel —
    it must fail immediately with an honest error, not an empty run."""
    from livekit.agents import APIConnectionError

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999")

    stream, emitter = await _make_synth_stream_with_input(t, [])

    with patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())):
        await stream._run(emitter)  # attempt 1 — empty input, completes

    with pytest.raises(APIConnectionError, match="cannot be retried"):
        await stream._run(emitter)  # framework retry — input already consumed


@pytest.mark.asyncio
@patch("livekit.plugins.techmo.tts._make_persistent_channel")
async def test_prewarm_after_aclose_is_noop(mock_make_channel: MagicMock) -> None:
    """prewarm() started after aclose() must not spawn a task that outlives teardown."""
    import asyncio

    from livekit.plugins.techmo.tts import TTS

    mock_make_channel.return_value = AsyncMock()

    t = TTS(service_address="localhost:9999")
    await t.aclose()

    t.prewarm()
    assert t._prewarm_task is None  # nothing spawned
    assert t._prewarm_done is not None
    await asyncio.wait_for(t._prewarm_done.wait(), timeout=1.0)  # waiters don't hang
    assert t._pool._entries == []  # nothing resurrected


@pytest.mark.asyncio
async def test_marker_rate_zero_mid_stream_uses_emitter_rate() -> None:
    """A mid-stream response with sampling_rate_hz=0 must resolve timing against the
    ESTABLISHED emitter rate, not the configured fallback — otherwise marker times
    drift when the emitter was initialized at a non-fallback rate."""
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

    from livekit.plugins.techmo.tts import TTS, TechmoChunkedStream

    # Fallback is 22050, but the service delivers 16000 — a later rate-0 response
    # must be interpreted as 16000, not 22050.
    t = TTS(service_address="localhost:9999", fallback_sample_rate=22050)

    r1 = MagicMock()
    r1.audio = b"\x00\x01" * 100
    r1.sampling_rate_hz = 16000
    r1.markers = []
    r2 = MagicMock()
    r2.audio = b"\x00\x01" * 100
    r2.sampling_rate_hz = 0  # unspecified — old-service behaviour
    r2.markers = [_make_marker_mock(8000, "m1")]

    async def _fake_streaming(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
        yield r1
        yield r2

    mock_stub = MagicMock()
    mock_stub.SynthesizeStreaming = _fake_streaming

    with (
        patch("livekit.plugins.techmo.tts.TTSStub", return_value=mock_stub),
        patch.object(t, "_get_channel", new=AsyncMock(return_value=MagicMock())),
    ):
        stream = TechmoChunkedStream(tts=t, input_text="test", conn_options=DEFAULT_API_CONNECT_OPTIONS)
        emitter = MagicMock()
        await stream._run(emitter)

    assert emitter.initialize.call_args.kwargs["sample_rate"] == 16000
    assert stream.markers[0].time_s == pytest.approx(8000 / 16000)  # 0.5 — not 8000/22050


@patch("livekit.plugins.techmo.tts.TTSStub")
@patch("livekit.plugins.techmo.tts._make_sync_channel")
def test_discover_sample_rate_closes_channel_on_error(mock_channel_fn: MagicMock, mock_stub_cls: MagicMock) -> None:
    """The sync discovery channel must be closed even when ListVoices raises."""
    from livekit.plugins.techmo.tts import TTSOptions, _discover_sample_rate

    mock_channel = MagicMock()
    mock_channel_fn.return_value = mock_channel
    mock_stub = MagicMock()
    mock_stub.ListVoices.side_effect = RuntimeError("rpc failed")
    mock_stub_cls.return_value = mock_stub

    opts = TTSOptions(service_address="localhost:9999", fallback_sample_rate=22050)
    rate = _discover_sample_rate(opts)

    assert rate == 22050  # fallback
    mock_channel.close.assert_called_once()  # no leaked channel


def test_marker_event_is_immutable() -> None:
    """MarkerEvent instances are shared with the on_marker callback and stored
    internally — they must be frozen so a consumer cannot corrupt .markers."""
    import dataclasses

    from livekit.plugins.techmo.tts import MarkerEvent

    ev = MarkerEvent(name="m1", time_s=0.5, position_samples=11025)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.time_s = 999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Voice / language defaults: left to the service unless given
# ---------------------------------------------------------------------------


def test_tts_voice_and_language_default_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.delenv("TECHMO_TTS_VOICE", raising=False)
    monkeypatch.delenv("TECHMO_TTS_LANGUAGE", raising=False)
    t = TTS(service_address="localhost:9999")
    assert t._opts.voice_name is None
    assert t._opts.language_code is None


def test_tts_voice_and_language_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_VOICE", "Masza")
    monkeypatch.setenv("TECHMO_TTS_LANGUAGE", "pl-PL")
    t = TTS(service_address="localhost:9999")
    assert t._opts.voice_name == "Masza"
    assert t._opts.language_code == "pl-PL"


def test_tts_empty_env_means_service_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from livekit.plugins.techmo.tts import TTS

    monkeypatch.setenv("TECHMO_TTS_VOICE", "")
    monkeypatch.setenv("TECHMO_TTS_LANGUAGE", "")
    t = TTS(service_address="localhost:9999")
    assert t._opts.voice_name is None
    assert t._opts.language_code is None


def test_build_request_without_voice_and_language_leaves_them_empty() -> None:
    # empty Voice.name / language_code: the service picks its configured default
    from livekit.plugins.techmo.tts import TTSOptions, _build_request

    req = _build_request("hello", TTSOptions(service_address="localhost:9999"))
    assert req.synthesis_config.voice.name == ""
    assert req.synthesis_config.language_code == ""


@pytest.mark.parametrize(
    ("voice", "available", "expected"),
    [
        ("Masza", ["Masza", "michal"], None),
        ("Maszka", ["Masza", "michal"], "not offered by the service"),
        ("MASZA", ["Masza", "masza"], "did you mean one of: Masza, masza"),
    ],
)
async def test_prewarm_warns_about_unknown_voice(
    voice: str, available: list[str], expected: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    from livekit.plugins.techmo.tts import TTS, VoiceInfo

    t = TTS(service_address="localhost:9999", voice_name=voice, output_sample_rate=16000)
    voices = [VoiceInfo(name=n, language_codes=["pl-PL"], variants=1) for n in available]
    with (
        patch.object(t._pool, "acquire", AsyncMock()),
        patch.object(t, "list_voices", AsyncMock(return_value=voices)),
        caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"),
    ):
        t.prewarm()
        await asyncio.wait_for(t._prewarm_done.wait(), timeout=5)
    voice_warnings = [r.getMessage() for r in caplog.records if "voice" in r.getMessage()]
    if expected is None:
        assert voice_warnings == []
    else:
        assert len(voice_warnings) == 1 and expected in voice_warnings[0], voice_warnings
    await t.aclose()


async def test_prewarm_voice_check_failure_is_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    from livekit.agents import APIConnectionError

    from livekit.plugins.techmo.tts import TTS

    t = TTS(service_address="localhost:9999", voice_name="Masza", output_sample_rate=16000)
    with (
        patch.object(t._pool, "acquire", AsyncMock()),
        patch.object(t, "list_voices", AsyncMock(side_effect=APIConnectionError("down"))),
        caplog.at_level(logging.WARNING, logger="livekit.plugins.techmo.tts"),
    ):
        t.prewarm()
        await asyncio.wait_for(t._prewarm_done.wait(), timeout=5)
    assert not [r for r in caplog.records if "voice" in r.getMessage()]
    await t.aclose()
