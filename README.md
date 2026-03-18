# livekit-plugins-techmo

[LiveKit Agents](https://github.com/livekit/agents) plugin for [Techmo ASR](https://techmo.pl) — a high-accuracy Polish and multilingual automatic speech recognition service exposed via gRPC.

## Features

- Streaming speech recognition (bidirectional gRPC)
- Batch recognition
- Interim (partial) results
- Word-level timestamps
- TLS and mutual-TLS support
- Multiple language groups / model selection
- Works with LiveKit `VoicePipelineAgent`

## Requirements

- Python >= 3.10
- `livekit-agents >= 1.0`
- `grpcio >= 1.63`
- `protobuf >= 5.0`
- Access to a running **Techmo ASR** gRPC server

## Installation

### From PyPI (when published)

```bash
pip install livekit-plugins-techmo
```

### From source

```bash
git clone https://github.com/techmo-pl/livekit-plugins-techmo
cd livekit-plugins-techmo

# Install build tools and generate gRPC stubs
pip install grpcio-tools
python hatch_build.py

# Install in editable mode
pip install -e ".[dev]"
```

> **Note:** The gRPC Python stubs are generated at install time from the `.proto` files
> in `proto/`. They are placed in `livekit/plugins/techmo/_proto/`.
> Run `make proto` (or `python hatch_build.py`) any time you change the `.proto` files.

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

### Inside a LiveKit Agent

```python
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins.techmo import STT

async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    agent = VoicePipelineAgent(
        stt=STT(language_group="pl"),
        # llm=..., tts=...
    )
    participant = await ctx.wait_for_participant()
    agent.start(ctx.room, participant)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
```

See `examples/` for more complete examples.

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
| `grpc_timeout` | `float \| None` | `None` | gRPC deadline in seconds |

## Development

```bash
# Generate gRPC stubs
make proto

# Run linter
make lint

# Run formatter
make format

# Run unit tests (no server required)
make test

# Run integration tests (requires TECHMO_ASR_ADDRESS)
TECHMO_ASR_ADDRESS=localhost:5555 pytest tests/test_integration.py -v
```

## API Version

This plugin uses the **Techmo ASR v1p1** gRPC API. The `.proto` definition is located at
`proto/techmo/asr/api/v1p1/asr.proto`.

## License

[Apache 2.0](LICENSE)
