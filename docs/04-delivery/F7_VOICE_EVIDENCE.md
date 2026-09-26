# F7 Telegram voice notes — builder evidence

Status: **IMPLEMENTED TO PROVIDER/CREDENTIAL BOUNDARIES; LIVE DARI E2E BLOCKED BY O-05/O-09**.

Base: F6 exact SHA `3069e87cc8f5e80c643a04ff19a32df7b002e308`.

Implemented:

- one Telegram webhook router that selects exactly one existing F2 text path or F7 voice path;
- voice ingress keeps the same webhook-secret, private-chat, replay, pairing, active-actor and audit boundaries as text;
- unpaired/revoked voice commands are denied before media download or STT;
- voice duration, declared size and MIME are bounded before download;
- Bot API `getFile` and media download use a runtime bot-token boundary, Telegram-only HTTPS host, redirect refusal, safe file-path validation and byte limits;
- binding/context are re-checked again immediately before media retrieval;
- provider-neutral STT and optional TTS interfaces;
- actor authorization is re-checked before and after STT/TTS provider calls;
- raw audio, transcript text and synthesized reply text are never persisted by the F7 service; audit stores only redacted/digested metadata;
- STT transcript and TTS artifact length/MIME outputs fail closed when invalid.

Acceptance limitation:

The code-level F7 boundary can be tested without selecting a provider. Full F7 acceptance still requires `O-05` (sanctioned Telegram sandbox credential/deployment) and the F7 portion of `O-09`: owner-approved STT/TTS provider/privacy/cost decision plus a Dari evaluation set. No provider accuracy claim is made before that evaluation.
