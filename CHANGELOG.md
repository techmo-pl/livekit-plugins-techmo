# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.4.0] - 2026-10-23

### Added
- `TTS`: Techmo TTS text-to-speech (gRPC API v3, stubs from `tts-api` 3.2.1), importable as
  `livekit.plugins.techmo.TTS` or `livekit.plugins.techmo.tts.TTS`. Native streaming synthesis
  (`TTS.stream()`, sentence by sentence) and chunked synthesis (`TTS.synthesize()`); voice,
  language and prosody options with `update_options()`; `list_voices()`; SSML `<mark/>` markers
  as `MarkerEvent` (`on_marker` callback); TLS / mutual TLS from bytes or file paths; gRPC
  channel pool with lifetime rotation, `prewarm()` and reconnect on transient `UNAVAILABLE`;
  `session_id` sent as `session_id` gRPC metadata. Ported from the internal Techmo plugin
  `livekit-plugins-techmo-tts` 0.3.2, with these differences:
  - no random `session_id` is generated when none is given (no header is sent, as with
    `tts-client-python`)
  - `voice_name` and `language_code` default to the service's configured voice and language
    instead of `"michal"` / `"pl-PL"`; `update_options(voice_name=None)` goes back to them
  - `prewarm()` warns when `voice_name` is not offered by the service (which would otherwise
    fall back to its default voice silently)
- `KNOWN_ISSUES.md`: limitations of older Techmo TTS service versions and their workarounds
- `examples/voice_agent.py`: voice agent with Techmo ASR + Techmo TTS
- CI job testing the lowest supported `livekit-agents` (1.2.0)

### Changed
- Dependencies: `livekit-agents>=1.2` (1.0/1.1 lack the sentence tokenizer TTS uses), `tts-api`

## [0.3.0] - 2026-10-22

### Fixed
- Fresh installs failing on import with `VersionError` (protobuf gencode newer than runtime):
  the build hook generated stubs with whatever `grpcio-tools` was newest, independent of the
  protobuf runtime in the environment
- `STT.recognize()` (batch recognition) raising `TypeError`: `_recognize_impl` did not accept
  the `conn_options` argument passed by livekit-agents, and failed on a single `AudioFrame`
- Editable installs (`pip install -e .`) shadowing `livekit.agents`: the empty
  `livekit/__init__.py` and `livekit/plugins/__init__.py` turned the shared namespace into a
  regular package; they are removed

### Changed
- gRPC stubs now come from the `techmo-asr-api` package (1.1.4) instead of being generated
  from `proto/` at build time; `hatch_build.py`, `proto/` and the `grpcio-tools` build
  dependency are removed, and `--no-build-isolation` is no longer needed
- Dependencies: `protobuf>=5.29.0` and `grpcio>=1.70.0` (required by the `techmo-asr-api`
  stubs), `livekit-agents<2.0`
- CI now runs on pushes and pull requests to `master` (it targeted a non-existent `main`)
- README requirements aligned with the package metadata
- Lint, format and mypy issues fixed so the CI passes; mypy no longer pins `python_version`

## [0.2.0] - 2026-09-30

### Added
- `session_id` option: sent as the `session-id` gRPC metadata header with streaming and
  batch recognition requests, so the service groups them into one session and its logs
  can be correlated with a LiveKit call

## [0.1.0] - 2026-03-18

### Added
- Initial release of `livekit-plugins-techmo`
- `STT` class implementing `livekit.agents.stt.STT` interface
- Streaming speech recognition via Techmo ASR gRPC v1p1 API
- Batch (non-streaming) recognition via `_recognize_impl`
- TLS and mutual-TLS support
- Configurable language group, model name, alternatives, word timing
- `TECHMO_ASR_ADDRESS` environment variable support
- Build-time gRPC stub generation via `hatchling` hook
- GitHub Actions CI pipeline
