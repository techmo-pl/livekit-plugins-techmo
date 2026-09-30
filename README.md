# livekit-plugins-techmo

[LiveKit Agents](https://github.com/livekit/agents) plugin for [Techmo ASR](https://techmo.pl) — a high-accuracy Polish and multilingual automatic speech recognition service exposed via gRPC.

## Features

- Streaming speech recognition (bidirectional gRPC)
- Batch recognition
- Interim (partial) results
- Word-level timestamps
- TLS and mutual-TLS support
- Multiple language groups / model selection
- MRCP timeout controls (no-input, recognition, speech-complete, speech-incomplete)
- Compatible with LiveKit Agents >= 1.5

## Requirements

- Python >= 3.10
- `livekit-agents >= 1.5`
- `grpcio >= 1.63`
- `protobuf >= 5.29`
- [`techmo-asr-api`](https://github.com/techmo-pl/asr-api-python) 1.1.4 (gRPC stubs; installed automatically)
- Access to a running **Techmo ASR** gRPC server

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
package (a dependency), so nothing is generated at install time.

## Quick Start

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

### Inside a LiveKit Agent (v1.5+)

```python
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions, WorkerOptions, cli
from livekit.plugins import silero
from livekit.plugins.techmo import STT

async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        vad=silero.VAD.load(),
        stt=STT(
            language_group="pl",
            interim_results=True,
            mrcp_speech_complete_timeout=1000,
        ),
        # llm=..., tts=...
    )

    await session.start(room=ctx.room, agent=Agent(), room_input_options=RoomInputOptions())

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

## Configuration Reference

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

### MRCP Timeout Notes

The four `mrcp_*` parameters map directly to MRCP speech recognition resource headers:

- **`mrcp_no_input_timeout`** — how long to wait for the user to start speaking before giving up
- **`mrcp_recognition_timeout`** — hard cap on total recognition time; set large (e.g. `600000`) for long utterances
- **`mrcp_speech_complete_timeout`** — silence duration after speech that ends the utterance when a grammar match is possible; smaller values make recognition feel more responsive (e.g. `1000`)
- **`mrcp_speech_incomplete_timeout`** — silence duration when no match is possible yet; typically larger than `speech_complete_timeout`

### Session ID

`session_id` is sent with every recognition request as the `session-id` gRPC metadata
header (the same header `asr-client-python --session-id` uses). All requests carrying the
same ID are treated by the service as one session, and service logs can be matched to the
call. Inside an agent, the LiveKit room name or job ID is a natural choice:

```python
stt = STT(language_group="pl", session_id=ctx.room.name)
```

Only printable ASCII is allowed (a gRPC metadata restriction); an empty string is treated as unset.

## Logging

The plugin uses the `livekit.plugins.techmo` logger. To see interim and final transcript events, enable `DEBUG` level logging. With LiveKit Agents this is done via the `LIVEKIT_LOG_LEVEL` environment variable:

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

# Run integration tests (requires TECHMO_ASR_ADDRESS)
TECHMO_ASR_ADDRESS=localhost:5555 pytest tests/test_integration.py -v
```

## API Version

This plugin uses the **Techmo ASR v1p1** gRPC API (`asr_api.v1p1` from
[`techmo-asr-api`](https://github.com/techmo-pl/asr-api-python), which also holds the `.proto` definitions).

## License

[Apache 2.0](LICENSE)
