# Known Issues — Techmo TTS Plugin

These are known limitations in the current Techmo TTS gRPC service that the plugin works around.

> **2026-07-02 verification:** issues 1–3 below were re-tested against a live service reporting
> version **3.2.4** (`GetServiceVersion`) and are **resolved** on that version. The workarounds
> remain documented for deployments running older service builds.

## 1. Sample rate not reported by ListVoices

**Status: resolved in service 3.2.4** (verified: `ListVoices.sampling_rate_hz = 22050`).

On older services the `ListVoices` RPC does not return the `sampling_rate_hz` field. The plugin
discovers the sample rate via a fallback mechanism:

- Attempts to read `sampling_rate_hz` from the `ListVoices` response
- Falls back to the `fallback_sample_rate` constructor parameter (default: 22050 Hz)

**Workaround (older services):** Set `fallback_sample_rate` in the TTS constructor if your service
uses a non-default sample rate. Additionally, the audio emitter is stamped with the per-response
`SynthesizeResponse.sampling_rate_hz`, so playback speed is correct even when discovery fails.

## 2. Max frame size not configurable server-side

**Status: resolved in service 3.2.4** (verified: `max_frame_size=512` → frames of exactly
512 samples).

On older services the maximum audio frame size is not negotiated with the server. The plugin
exposes a `max_frame_size` constructor parameter (default: `0`, meaning service default).

**Workaround (older services):** Leave `max_frame_size=0`.

## 3. Output sample rate may differ from requested

**Status: resolved in service 3.2.4** (verified: requesting 16000 Hz returns responses reporting
16000 Hz). A non-zero `output_sample_rate` now also sets the plugin's `sample_rate`.

On older services the actual output sample rate may differ from the rate requested in
`OutputConfig`.

**Workaround (older services):** The emitter uses the actual per-response
`SynthesizeResponse.sampling_rate_hz`, so a service that ignores the requested rate still plays
back correctly (at its native rate). The LiveKit Agents framework resamples when the agent
session requires a different rate.

## 4. No automatic word-level timestamps (SSML `<mark/>` markers DO work)

**Verified 2026-07-02 on service 3.2.4:** `SynthesizeResponse.markers` **is populated**
when the input text contains SSML mark tags — e.g.
`<speak>Witaj <mark name="m1"/> świecie</speak>` returns `Marker{position (samples), name}`
entries. Plain-text input returns no timing data.

**Plugin support:** markers are exposed as `MarkerEvent(name, time_s, position_samples)` —
collected on `TechmoChunkedStream.markers` / `TechmoSynthesizeStream.markers` and delivered
via the `TTS(on_marker=...)` callback. They are deliberately NOT pushed through
`output_emitter.push_timed_transcript()` — `TimedString` is transcript *text*, and marker
names like "m1" would pollute user-facing transcripts.

**Remaining gap:** *automatic* per-word timestamps (without hand-annotated SSML) — what
Cartesia (`word_timestamps`) and ElevenLabs (`normalizedAlignment`) provide. Requested from
the TTS service team. Once available, wire it via `output_emitter.push_timed_transcript(TimedString(...))` (real
words are transcript text, unlike marker names).
