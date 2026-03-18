# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
