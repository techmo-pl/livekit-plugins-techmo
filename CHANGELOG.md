# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- Fresh installs failing on import with `VersionError` (protobuf gencode newer than runtime):
  the build hook generated stubs with whatever `grpcio-tools` was newest, independent of the
  protobuf runtime in the environment

### Changed
- gRPC stubs now come from the `techmo-asr-api` package (1.1.4) instead of being generated
  from `proto/` at build time; `hatch_build.py`, `proto/` and the `grpcio-tools` build
  dependency are removed, and `--no-build-isolation` is no longer needed
- Dependencies: `protobuf>=5.29.0` (required by the `techmo-asr-api` stubs),
  `livekit-agents<2.0`

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
