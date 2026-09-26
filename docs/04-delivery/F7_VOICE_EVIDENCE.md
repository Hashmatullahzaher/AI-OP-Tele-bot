# F7 Voice — builder evidence

Status: **PROVIDER-NEUTRAL STT/TTS CORE IMPLEMENTED; LIVE TELEGRAM AUDIO FETCH + PROVIDER/DARI EVALUATION REMAIN BLOCKED BY O-09/O-05**.

Base SHA: `3069e87cc8f5e80c643a04ff19a32df7b002e308`
Branch: `app-maker/f7-voice`

## Implemented

- provider-neutral speech-to-text protocol;
- optional provider-neutral text-to-speech protocol;
- trusted `ExecutionContext` is re-authorized against the tenant store before every STT/TTS operation;
- platform operators and revoked actors fail closed for tenant voice operations;
- provider adapters do not receive tenant ID or actor ID;
- bounded audio bytes, duration, MIME types and language hints;
- bounded transcript/TTS text and bounded returned audio;
- raw audio and transcript text are not persisted by the voice core;
- audit stores only redacted metadata/digests, provider name, lengths and language information;
- provider errors are normalized to sanitized voice availability failures;
- TTS remains optional and cannot silently activate without a configured provider.

## Builder verification

```text
python -m compileall -q osai tests
python -m unittest tests.test_voice -v
RESULT: 7/7 PASS
```

Full GitHub CI must pass on the exact F7 SHA before handoff.

## Acceptance coverage

Automated tests prove:

- authorized paired-style tenant context can transcribe;
- revoked actor receives the same fail-closed access boundary;
- platform operator cannot access tenant voice;
- provider never receives trusted tenant/actor identity;
- raw transcript is absent from persistent audit storage;
- invalid MIME/size/duration/language fails closed;
- optional TTS re-checks identity and validates provider output.

## Exact remaining live blockers

- `O-09`: choose sanctioned STT/TTS provider(s), privacy/data residency/cost policy, and an owner-approved Dari evaluation corpus; measure transcription quality against that corpus.
- `O-05`: live Telegram bot/public endpoint is still required before fetching real Telegram `voice` files and running end-to-end voice-note UAT.

This milestone does not fabricate a provider choice or claim live Telegram voice-note certification.
