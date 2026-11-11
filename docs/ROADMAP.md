# Roadmap and known gaps

This page lists what is **not implemented** in the current code (version 0.1.0). Items are grouped by area. Nothing here should be read as existing functionality.

## Known gaps

| Area | Gap | Current behaviour |
|---|---|---|
| Voice | **Optional speaker verification is not implemented** | Any voice that triggers the wake word or speaks during push-to-talk is accepted |
| Voice | **RNNoise is not used** | A NumPy spectral gate (80 Hz high-pass + spectral gating) runs once per utterance. RNNoise has no maintained Windows Python wheels ([VOICE_PIPELINE.md](VOICE_PIPELINE.md#5-noise-reduction-noisepy)) |
| Voice | **Urdu / Roman Urdu not tuned** | Selecting Urdu only passes `ur` as Whisper's `language`; "auto" lets Whisper detect. No Urdu prompts, post-processing, TTS voice or wake word |
| Voice | Custom wake phrases | Only the pretrained "Hey Jarvis" model; a bare "Jarvis" may or may not trigger it |
| Email | **No email send tool (by design)** | Drafts only (`create_email_draft`); no `gmail.send` scope requested |
| Email | Labels, archive, delete, attachment download, multiple accounts | Not implemented |
| Terminal | **Inspecting terminal output only works for processes Jarvis started** (`run_command`, `run_project`) | Arbitrary existing terminals cannot be read; `inspect_process` gives status, CPU, memory and command line for any PID |
| Packaging | **Code signing of the installer is not configured** | Unsigned NSIS/MSI and executables; SmartScreen warnings expected |
| Browser | **The Playwright `chromium` channel requires `playwright install chromium`, which is not bundled** | Default channels `msedge`/`chrome` use the installed browsers |
| Providers | Only Groq | `ProviderManager`/`GroqClient` are Groq-specific; see [TOOL_DEVELOPMENT.md](TOOL_DEVELOPMENT.md#how-to-add-another-ai-provider) for a path |
| Data | No automatic retention or pruning | `usage`, `tasks`, `execution_logs` grow until cleared via `/api/data/clear` |
| Updates | No auto-update | Upgrades are manual: install a newer NSIS/MSI |

## Candidate next steps

These are derived from the gaps above and are not commitments.

1. Configure Windows code signing in electron-builder and sign `jarvis-backend.exe`.
2. Speaker verification as an optional gate after the wake word.
3. Urdu/Roman Urdu evaluation: a Whisper prompt or language handling, an Urdu TTS voice if a suitable Piper voice exists.
4. Provider abstraction (pluggable quota header semantics, provider-aware model preferences).
5. Data retention settings for usage and audit logs.
6. Optional bundling (or an in-app installer) for the Playwright Chromium channel.

Build, installer and performance verification results are tracked in `IMPLEMENTATION_PROGRESS.md`.
