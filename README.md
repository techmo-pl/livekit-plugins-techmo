# livekit-plugins-techmo

[LiveKit Agents](https://github.com/livekit/agents) plugin for [Techmo](https://techmo.pl) speech services exposed via gRPC:

- **`STT`** — Techmo ASR, a high-accuracy Polish and multilingual automatic speech recognition service
- **`TTS`** — Techmo TTS text-to-speech synthesis

Both target real-time voicebot pipelines (STT → LLM → TTS) and are importable from `livekit.plugins.techmo`.

## Features

**ASR (`STT`)**

- Streaming speech recognition (bidirectional gRPC)
- Batch recognition
- Interim (partial) results
- Word-level timestamps
- TLS and mutual-TLS support
- Multiple language groups / model selection
- MRCP timeout controls (no-input, recognition, speech-complete, speech-incomplete)

**TTS (`TTS`)**

- Native streaming synthesis (`TTS.stream()`): text is split into sentences and each is synthesized as soon as it is complete
- Chunked synthesis (`TTS.synthesize()`) via the `SynthesizeStreaming` gRPC call
- Prosody control: pitch, rate, range, stress, volume
- Multiple voices and voice variants; `list_voices()`
- SSML `<mark/>` markers reported as `MarkerEvent`s
- TLS and mutual-TLS support (bytes or file paths)
- gRPC channel pool with lifetime-based rotation, `prewarm()`, reconnect on transient `UNAVAILABLE`
- Sample rate auto-discovery via `ListVoices`

Compatible with LiveKit Agents 1.x (>= 1.2).

## Requirements

- Python >= 3.10
- `livekit-agents >= 1.2, < 2.0` (tested with 1.2.0, 1.5.1 and 1.8.8)
- `grpcio >= 1.70`
- `protobuf >= 5.29`
- gRPC stubs (installed automatically):
  [`techmo-asr-api`](https://github.com/techmo-pl/asr-api-python) 1.1.4 and
  [`tts-api`](https://github.com/techmo-pl/tts-api-python) 3.2.1
- Access to a running **Techmo ASR** and/or **Techmo TTS** gRPC server

## Installation

```bash
pip install "livekit-plugins-techmo @ git+https://github.com/techmo-pl/livekit-plugins-techmo.git@<version-tag>"
```

or from a clone:

```bash
git clone https://github.com/techmo-pl/livekit-plugins-techmo
cd livekit-plugins-techmo
pip install .
```

The gRPC stubs come from the [`techmo-asr-api`](https://github.com/techmo-pl/asr-api-python)
and [`tts-api`](https://github.com/techmo-pl/tts-api-python) packages (dependencies), so nothing
is generated at install time.

## Speech-to-Text (ASR)

### Quick Start

```python
from livekit.plugins.techmo import STT

stt = STT(
    service_address="asr.example.com:5555",
    language_group="pl",     # Polish; omit to use server default
    interim_results=True,
)
```

Or set the address via environment variable:

```bash
export TECHMO_ASR_ADDRESS=asr.example.com:5555
```

```python
from livekit.plugins.techmo import STT

stt = STT()  # reads TECHMO_ASR_ADDRESS automatically
```

### With TLS

```python
stt = STT(
    service_address="asr.example.com:443",
    tls=True,
    ca_cert=open("ca.crt", "rb").read(),
    # For mutual TLS:
    # client_cert=open("client.crt", "rb").read(),
    # client_key=open("client.key", "rb").read(),
)
```

### Inside a LiveKit Agent

```python
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions, WorkerOptions, cli
from livekit.plugins import openai, silero
from livekit.plugins.techmo import STT, TTS

async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=STT(
            language_group="pl",
            interim_results=True,
            mrcp_speech_complete_timeout=1000,
            session_id=ctx.room.name,
        ),
        llm=openai.LLM(),
        tts=TTS(voice_name="ewa", session_id=ctx.room.name),
    )

    await session.start(room=ctx.room, agent=Agent(instructions="..."), room_input_options=RoomInputOptions())

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

See [`examples/voice_agent.py`](examples/voice_agent.py) for a runnable version.

### Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_address` | `str` | `TECHMO_ASR_ADDRESS` env | gRPC server address (`host:port`) |
| `sample_rate` | `int` | `16000` | Audio sample rate in Hz |
| `language_group` | `str \| None` | `None` | Language group name (server default if unset) |
| `model_name` | `str \| None` | `None` | Model name (language group default if unset) |
| `interim_results` | `bool` | `True` | Return partial transcripts during speech |
| `single_utterance` | `bool` | `False` | Stop after first complete utterance |
| `max_alternatives` | `int` | `1` | Maximum recognition alternatives |
| `enable_word_timing` | `bool` | `False` | Include word-level timestamps |
| `tls` | `bool` | `False` | Use TLS for connection |
| `ca_cert` | `bytes \| None` | `None` | PEM CA certificate for TLS |
| `client_cert` | `bytes \| None` | `None` | PEM client certificate (mutual TLS) |
| `client_key` | `bytes \| None` | `None` | PEM client private key (mutual TLS) |
| `grpc_timeout` | `float \| None` | `None` | Overall gRPC deadline in seconds |
| `session_id` | `str \| None` | `None` | Session ID sent as `session-id` gRPC metadata (service generates one per request if unset) |
| `mrcp_no_input_timeout` | `int \| None` | `None` | ms of silence before NO_INPUT_TIMEOUT (server default if unset) |
| `mrcp_recognition_timeout` | `int \| None` | `None` | Maximum total utterance duration in ms (server default if unset) |
| `mrcp_speech_complete_timeout` | `int \| None` | `None` | Silence after speech (match expected) in ms (server default if unset) |
| `mrcp_speech_incomplete_timeout` | `int \| None` | `None` | Silence after speech (no match yet) in ms (server default if unset) |

#### MRCP Timeout Notes

The four `mrcp_*` parameters map directly to MRCP speech recognition resource headers:

- **`mrcp_no_input_timeout`** — how long to wait for the user to start speaking before giving up
- **`mrcp_recognition_timeout`** — hard cap on total recognition time; set large (e.g. `600000`) for long utterances
- **`mrcp_speech_complete_timeout`** — silence duration after speech that ends the utterance when a grammar match is possible; smaller values make recognition feel more responsive (e.g. `1000`)
- **`mrcp_speech_incomplete_timeout`** — silence duration when no match is possible yet; typically larger than `speech_complete_timeout`

#### Session ID

`session_id` is sent with every recognition request as the `session-id` gRPC metadata
header (the same header `asr-client-python --session-id` uses). All requests carrying the
same ID are treated by the service as one session, and service logs can be matched to the
call. Inside an agent, the LiveKit room name or job ID is a natural choice:

```python
stt = STT(language_group="pl", session_id=ctx.room.name)
```

Only printable ASCII is allowed (a gRPC metadata restriction); an empty string is treated as unset.


## Text-to-Speech (TTS)

### Quick Start

```python
from livekit.plugins.techmo import TTS

tts = TTS(service_address="tts.example.com:5555", voice_name="ewa", language_code="pl-PL")
```

Or set the defaults via environment variables and omit them:

```bash
export TECHMO_TTS_ADDRESS=tts.example.com:5555
export TECHMO_TTS_VOICE=ewa          # default: michal
export TECHMO_TTS_LANGUAGE=pl-PL     # default: pl-PL
```

```python
tts = TTS()  # reads TECHMO_TTS_ADDRESS, TECHMO_TTS_VOICE, TECHMO_TTS_LANGUAGE
```

`TTS` declares streaming support, so `AgentSession(tts=TTS(...))` uses the native
`TTS.stream()` path: LLM output is split into sentences, and each sentence is synthesized as
soon as it is complete (no `StreamAdapter` needed). `TTS.synthesize(text)` synthesizes one
piece of text. `livekit.plugins.techmo.tts` is also importable as a module
(`from livekit.plugins.techmo.tts import TTS`).

### Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `service_address` | `str \| None` | `TECHMO_TTS_ADDRESS` env | gRPC server address (`host:port`) |
| `voice_name` | `str \| None` | `TECHMO_TTS_VOICE` env, else `"michal"` | Voice name (e.g. `"michal"`, `"ewa"`) |
| `voice_variant` | `int` | `1` | Voice variant index (1-based) |
| `language_code` | `str \| None` | `TECHMO_TTS_LANGUAGE` env, else `"pl-PL"` | BCP-47 language code |
| `model` | `str` | `"standard"` | Model/backend identifier, reported to framework telemetry |
| `pitch` | `float` | `1.0` | Pitch multiplier |
| `speech_range` | `float` | `1.0` | Pitch range multiplier |
| `rate` | `float` | `1.0` | Speaking rate multiplier |
| `stress` | `float` | `1.0` | Stress multiplier |
| `volume` | `float` | `1.0` | Volume multiplier |
| `tls` | `bool` | `False` | Use TLS for the connection |
| `ca_cert` / `ca_cert_file` | `bytes` / `str` | `None` | PEM CA certificate, as bytes or a file path |
| `client_cert` / `client_cert_file` | `bytes` / `str` | `None` | PEM client certificate (mutual TLS) |
| `client_key` / `client_key_file` | `bytes` / `str` | `None` | PEM client private key (mutual TLS) |
| `session_id` | `str \| None` | `None` | Session ID sent as `session_id` gRPC metadata (not sent if unset; the service assigns its own) |
| `grpc_timeout` | `float \| None` | `None` | Per-call gRPC deadline in seconds (falls back to `conn_options.timeout`, then 30 s) |
| `output_sample_rate` | `int` | `0` | Requested output sample rate in Hz; `0` = service native |
| `max_frame_size` | `int` | `0` | Max audio frame size in samples per response; `0` = service default |
| `fallback_sample_rate` | `int` | `22050` | Sample rate used when `ListVoices` does not report one |
| `session_lifetime_s` | `float` | `300.0` | Recreate a pooled gRPC channel after this many seconds |
| `pool_size` | `int` | `1` | Number of parallel gRPC channels (raise for concurrent synthesis) |
| `on_marker` | `Callable[[MarkerEvent], None] \| None` | `None` | Callback for SSML `<mark/>` markers |

`update_options()` changes voice, language, prosody, `model` and `grpc_timeout` of an existing
instance; `list_voices()` returns the voices the service offers (`VoiceInfo`).

#### Prosody

All prosody parameters are multipliers relative to the voice's default:

```python
tts = TTS(voice_name="ewa", rate=1.2, pitch=0.9, volume=1.5)  # 20% faster, slightly lower, louder
```

Values outside the service range log a warning: `pitch` and `rate` 0.1–4.0; `speech_range`,
`stress` and `volume` 0.0–4.0.

#### TLS

```python
tts = TTS(
    service_address="tts.example.com:443",
    tls=True,
    ca_cert_file="certs/ca.crt",
    # For mutual TLS:
    # client_cert_file="certs/client.crt",
    # client_key_file="certs/client.key",
)
```

Bytes arguments (`ca_cert=...`) take precedence over the `*_file` ones. Passing certificates
without `tls=True` enables TLS and logs a warning.

#### SSML markers

When the input contains SSML mark tags, the service reports their positions; the plugin
converts them to `MarkerEvent(name, time_s, position_samples)`. They are delivered to the
`on_marker` callback and collected on the stream's `markers` list:

```python
def on_marker(ev):
    print(f"{ev.name} at {ev.time_s:.3f}s")

tts = TTS(on_marker=on_marker)
async with tts.synthesize('<speak>Witaj <mark name="m1"/> świecie</speak>') as stream:
    async for audio in stream:
        ...
```

Exceptions raised by the callback are logged, not propagated into synthesis.

#### Warm-up

`tts.prewarm()` opens the gRPC channel and checks the service sample rate in the background,
so the first synthesis does not pay the connection cost. LiveKit Agents calls it when the
session starts.

#### Session ID

`session_id` is sent with every synthesis request as the `session_id` gRPC metadata header
(note: underscore, the Techmo TTS service convention also used by `tts-client-python`; the ASR
plugin uses `session-id`). If it is not set, no header is sent and the service assigns its own
ID. Pass the same value as for `STT` to correlate ASR and TTS service logs of one call.

#### Known Issues

See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for limitations of older Techmo TTS service versions
and the workarounds the plugin applies.

## Logging

The plugin uses the `livekit.plugins.techmo` logger (TTS records come from its child `livekit.plugins.techmo.tts`). To see interim and final transcript events, enable `DEBUG` level logging. With LiveKit Agents this is done via the `LIVEKIT_LOG_LEVEL` environment variable:

```bash
LIVEKIT_LOG_LEVEL=DEBUG python my_agent.py dev
```

## Development

```bash
# Run linter
ruff check .

# Run formatter
ruff format .

# Run unit tests (no server required)
pytest tests/ -v

# Run integration tests (require a running service)
TECHMO_ASR_ADDRESS=localhost:5555 pytest tests/test_integration.py -v
TECHMO_TTS_ADDRESS=localhost:5556 pytest tests/test_tts_integration.py -v
```

## API Version

- ASR: **Techmo ASR v1p1** gRPC API (`asr_api.v1p1` from
  [`techmo-asr-api`](https://github.com/techmo-pl/asr-api-python), which also holds the `.proto` definitions)
- TTS: **Techmo TTS v3** gRPC API (`tts_service_api.v3` from
  [`tts-api`](https://github.com/techmo-pl/tts-api-python)); service 3.2.4 or newer recommended, see
  [KNOWN_ISSUES.md](KNOWN_ISSUES.md)

## License

[Apache 2.0](LICENSE)
