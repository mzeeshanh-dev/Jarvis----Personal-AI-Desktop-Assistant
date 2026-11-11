# Voice pipeline

Code: `backend/voice/` (`audio.py`, `vad.py`, `noise.py`, `wakeword.py`, `tts.py`, `pipeline.py`), renderer playback in `frontend/src/renderer/lib/playback.ts`.

```
 triggers: Ctrl+Space (Electron global shortcut → POST /api/voice/toggle)
           mic button (WS voice.toggle) · wake word "Hey Jarvis" (local)
                         |
                         v
   MicrophoneStream (sounddevice/PortAudio, 16 kHz mono int16, 512-sample = 32 ms frames)
        |  opened on demand, released when no subscriber remains
        v
   VAD per frame (Silero ONNX, or energy fallback)  --> voice.level (~12 Hz)
        |  pre-roll 10 frames; start at prob >= vad_threshold; end after silence_ms
        v
   utterance (int16)  --> noise reduction (80 Hz HPF + spectral gate, once)  [optional]
        v
   WAV --> Groq Whisper (/audio/transcriptions, via ProviderManager)    [cloud]
        v
   hallucination filter --> voice.transcript --> Orchestrator.submit(source="voice")
        v
   final reply --> speakable() --> Piper TTS (local) --> tts.ready --> renderer <audio>
                                    \--fallback--> tts.system --> renderer speechSynthesis
```

Only the finished utterance is uploaded, to Groq for transcription. Wake-word detection, VAD, noise reduction and Piper TTS all run locally.

## Why the backend owns the microphone

The renderer is denied every permission, the microphone included (`setPermissionRequestHandler(... cb(false))`). Capturing in Python means push-to-talk and the wake word work while every window is hidden, and the renderer never needs device access. See [ARCHITECTURE.md](ARCHITECTURE.md#major-design-decisions).

## Settings (`VoiceSettings` in `core/config.py`)

| Setting | Default | Allowed (backend) | UI slider | Meaning |
|---|---|---|---|---|
| `input_device` | `null` (system default) | PortAudio device index | device list | |
| `wake_word_enabled` | `false` | | toggle | |
| `wake_word_threshold` | `0.5` | 0.05–0.99 | 0.2–0.9 | Classifier score needed to trigger |
| `vad_threshold` | `0.5` | 0.1–0.95 | 0.2–0.9 | Speech start probability |
| `silence_ms` | `900` | 300–4000 | 400–3000 | Trailing silence that ends the utterance |
| `max_record_s` | `30` | 3–120 | 5–120 | Hard cap since the session started |
| `no_speech_timeout_s` | `7` | 2–30 | 2–20 | Give up if no speech starts |
| `noise_reduction` | `true` | | toggle | Spectral gate before upload |
| `language` | `"en"` | free string | en / ur / auto | Passed to Whisper; `""`/`"auto"` → omitted (auto-detect) |
| `tts_engine` | `"piper"` | piper / system / off | | |
| `piper_voice` | `"en_GB-alan-medium"` | | | Model file `piper/<voice>.onnx` |
| `speak_responses` | `true` | | | Speak replies to voice commands |
| `speak_typed_responses` | `false` | | | Also speak replies to typed commands |
| `tts_rate` | `1.0` | 0.5–2.0 | 0.7–1.5 | Piper `length_scale = 1/rate`; system voice `rate` |

Model selection: `providers.models.transcription` (default `whisper-large-v3-turbo`).

## Step by step

### 1. Trigger

- **Push-to-talk**: `VoiceService.toggle()`. If a session is listening, toggling sets the stop flag, which ends recording and transcribes what was captured. If a session is already transcribing, the call returns its state. Otherwise it starts a new session.
- `start()` emits **`tts.stop`** first (barge-in: any speech playing is stopped), except for wake-word sessions (`interrupt=False`).
- The UI's Stop button sends `task.cancel_all`, which also cancels the voice session.

### 2. Microphone (`audio.py`)

- `sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=512)`. If the device rejects 16 kHz, the stream reopens at the device's native rate and each block is resampled by linear interpolation (`np.interp`).
- The PortAudio callback hands data to the event loop (`call_soon_threadsafe`). It is re-framed into 512-sample frames and fanned out to subscriber queues. The session queue holds 400 frames and the wake-word queue 100; a full queue drops its oldest frame.
- The device list (`/api/voice/devices`) prefers **MME** host-API entries, which resample to 16 kHz reliably, and de-duplicates by the first 31 characters of the name.
- `check_microphone()` (`/api/voice/test-mic`) opens the mic for 0.6 s. An all-zero peak means "Microphone returns silence…", which usually points to the Windows privacy setting.
- The mic is **closed whenever no subscriber remains** (`_release_mic_if_idle`). With the wake word enabled it stays open.

### 3. Voice activity detection (`vad.py`)

- **Silero VAD** (`resources/models/vad/silero_vad.onnx`) on onnxruntime CPU with 1 intra-op and 1 inter-op thread. It carries a 64-sample context and a `(2,1,128)` state between frames.
- **EnergyVAD** fallback, used when the model is missing or fails to load: an adaptive noise floor in dB, `prob = clip((db - floor - 6)/12, 0, 1)`.

### 4. Capture (`VoiceService._capture`)

- Before speech starts, frames go to a 10-frame (~320 ms) **pre-roll** buffer. When `prob >= vad_threshold`, recording starts with the pre-roll included.
- Before speech: the stop flag ends the session with no result. After `no_speech_timeout_s` the server emits `voice.no_speech`.
- During speech, a frame counts as silence when `prob < max(0.15, vad_threshold - 0.15)`, so there is hysteresis. Recording ends when silence reaches `silence_ms`, when `max_record_s` has elapsed since the session started, or when the stop flag is set.
- Less than 250 ms of speech emits `voice.no_speech`. If no frame arrives for 1.5 s, the server emits `voice.error` "Microphone stopped delivering audio."
- The server emits `voice.level` (RMS mapped from -60…-10 dB to 0..1) at most every 80 ms.

### 5. Noise reduction (`noise.py`)

The gate runs **once per utterance** in a worker thread, never continuously. Utterances shorter than 2048 samples are returned unchanged.
1. FFT-domain high-pass with a ramp from 40 Hz to 80 Hz.
2. STFT (Hann, N_FFT 512, hop 128). The noise profile is the 30th percentile of each frequency bin over time.
3. Mask = `clip((|X| - 2.0·noise) / |X|, 0, 1)`, smoothed over time with a `[0.25, 0.5, 0.25]` kernel, with a floor of 0.15.
4. Overlap-add resynthesis.

This is a NumPy spectral gate. **RNNoise is not used**, because it has no maintained Windows Python wheels.

### 6. Transcription

- `to_wav()` produces 16-bit mono 16 kHz WAV, which is posted to Groq `/audio/transcriptions` with `response_format=json`, `temperature=0` and `language` when set. It goes through the same key rotation and failover as chat (see [GROQ_PROVIDER_MANAGER.md](GROQ_PROVIDER_MANAGER.md)).
- Usage accounting records `max(10, duration)` audio seconds, because Groq bills a 10 s minimum per request.
- If no key is available, `voice.error` carries the provider's user message, for example "No Groq API key is configured…".
- **Hallucination filter**: an empty result, or text matching one of {"thank you", "thanks for watching", "thank you for watching", "you", "bye", ".", "so", "okay", "subtitles by the amara.org community", "please subscribe"} after punctuation is stripped, is discarded as `voice.no_speech`.
- Latency samples: `stt_ms`, plus `voice_preprocess_ms` (the time spent outside the STT call).

### 7. Hand-off to the agent

The server emits `voice.transcript`, sets the state to idle, and calls `Orchestrator.submit(text, conversation_id, source="voice")`. `conversation_id` is the one the main window last reported via `ui.conversation`. The service then follows the new task's conversation id so voice follow-ups continue the same conversation.

### 8. Spoken reply

`Orchestrator._finish` speaks the reply if `tts_engine != "off"` and either the task came from voice with `speak_responses` on, or `speak_typed_responses` is on. Cancelled tasks are not spoken.

`speakable()` removes code blocks ("(code omitted)"), inline code markers, images, link markup, headings, emphasis and bullets, replaces URLs with "a link", and caps the text at 700 characters. When it truncates, it cuts at a sentence end if one falls after 200 characters, then appends "The full answer is on screen."

`VoiceService.speak()`:
- **Piper** (`tts.py`), used when the engine is `piper`, the `piper` package imports, and `piper/<voice>.onnx` exists. The voice loads lazily once and synthesis runs in a thread under a lock. The WAV is cached under a 12-hex `clip_id`, and the cache keeps the last 12 clips. The server emits `tts.ready`. Latency sample: `tts_ms`.
- Otherwise, or if Piper throws, the server emits **`tts.system`** with the text and rate.

Renderer (`playback.ts`, **main window only**):
- `tts.ready` → `new Audio(<backend>/api/tts/<clip>.wav?token=…)`.
- `tts.system` → `speechSynthesis` with a preferred `en-GB` voice, else any `en*` voice.
- Controls: pause, resume, stop, replay (the playback bar). `tts.stop` stops playback.
- Playback state goes to the backend as `tts.state` and comes back to all windows as `tts.playing`, which drives the float window's "speaking" orb.

## Wake word ("Hey Jarvis")

Enabled by `wake_word_enabled` and not paused (tray → *Pause listening* → `POST /api/voice/pause`). `apply_settings()` starts or stops the loop whenever settings change.

- The phrase is **"Hey Jarvis"**: the pretrained openWakeWord `hey_jarvis_v0.1` model. A bare "Jarvis" may or may not trigger it.
- Models: `wakeword/melspectrogram.onnx`, `embedding_model.onnx`, `hey_jarvis_v0.1.onnx`, all loaded once in a thread. If any is missing: `voice.error` "Wake word unavailable: wake-word models are missing (run npm run setup)", and `wake_word_error` in the status.
- Streaming features (`wakeword.py`, a re-implementation of openWakeWord's pipeline on onnxruntime):
  - Audio is processed in 1280-sample (80 ms) chunks, with a 480-sample overlap kept for the melspectrogram.
  - Melspectrogram output is transformed as `x/10 + 2`, the last 76 mel frames feed the embedding model (96-d), and the last N embeddings (the classifier input length, 16 if unspecified) feed the classifier.
  - The first 10 chunks (~0.8 s) are warm-up and scored 0. A detection starts a 25-chunk (~2 s) cooldown.
- Per frame, scoring runs in a worker thread. Frames are ignored while a command session is running.
- On detection: emit `wakeword.detected`, speak "Yes?" (`kind="ack"`), wait 0.55 s, drain the queue, then start a session with `source="wake"` and no barge-in.
- Score threshold: `wake_word_threshold` (default 0.5).

## Status

`GET /api/voice/status` and the `hello` event return `state`, `paused`, `wake_word_enabled`, `wake_word_running`, `wake_word_error`, `vad` (`silero`/`energy`), `piper_available`, `piper_voice` and `mic_open`.

## Languages

English is the default (`language: "en"`). **Urdu and Roman Urdu are not tuned.** Selecting Urdu only passes `ur` to Whisper as its language setting, and "auto" lets Whisper detect the language. There is no Urdu-specific prompt, post-processing, TTS voice or wake word.

## Not implemented

- **Speaker verification** (accepting commands only from the owner's voice) is not implemented.
- RNNoise is not used; see step 5.
- See [ROADMAP.md](ROADMAP.md).
