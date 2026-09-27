"""Provider-neutral speech-to-text and optional text-to-speech boundary for F7."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from .contracts import ExecutionContext
from .storage import TenantSecurityStore


class VoiceError(RuntimeError):
    """Base class for sanitized voice-processing failures."""


class VoiceProviderUnavailable(VoiceError):
    """The configured STT/TTS provider is unavailable."""


class VoiceInputDenied(VoiceError):
    """Input or provider output failed a fail-closed validation rule."""


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str


@dataclass(frozen=True)
class SpeechArtifact:
    content: bytes
    mime_type: str


class SpeechToTextProvider(Protocol):
    def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        language_hint: str | None,
        duration_seconds: int,
    ) -> TranscriptionResult:
        """Transcribe one bounded audio note without persisting it."""


class TextToSpeechProvider(Protocol):
    def synthesize(self, *, text: str, language: str) -> SpeechArtifact:
        """Synthesize one bounded reply."""


class VoiceService:
    """Apply actor checks, bounded provider calls, and redacted audit."""

    _INPUT_MIMES = frozenset({"audio/ogg", "application/ogg"})
    _OUTPUT_MIMES = frozenset({"audio/ogg", "audio/mpeg"})

    def __init__(
        self,
        *,
        store: TenantSecurityStore,
        stt_provider: SpeechToTextProvider,
        tts_provider: TextToSpeechProvider | None = None,
        max_audio_bytes: int = 20_000_000,
        max_duration_seconds: int = 300,
        max_transcript_chars: int = 4_096,
        max_tts_bytes: int = 20_000_000,
    ) -> None:
        self.store = store
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.max_audio_bytes = max_audio_bytes
        self.max_duration_seconds = max_duration_seconds
        self.max_transcript_chars = max_transcript_chars
        self.max_tts_bytes = max_tts_bytes

    def transcribe(
        self,
        *,
        context: ExecutionContext,
        audio: bytes,
        mime_type: str,
        duration_seconds: int,
        language_hint: str | None = "fa-AF",
    ) -> TranscriptionResult:
        self.store.assert_actor(context, tenant_data=True)
        if not audio or len(audio) > self.max_audio_bytes:
            raise VoiceInputDenied("voice input size denied")
        if duration_seconds < 1 or duration_seconds > self.max_duration_seconds:
            raise VoiceInputDenied("voice duration denied")
        if mime_type not in self._INPUT_MIMES:
            raise VoiceInputDenied("voice MIME type denied")
        result = self.stt_provider.transcribe(
            audio=audio,
            mime_type=mime_type,
            language_hint=language_hint,
            duration_seconds=duration_seconds,
        )
        clean = result.text.strip()
        if not clean or len(clean) > self.max_transcript_chars:
            raise VoiceInputDenied("voice transcript denied")
        if any(ord(ch) < 32 and ch not in {"\n", "\t"} for ch in clean):
            raise VoiceInputDenied("voice transcript denied")
        language = result.language.strip() or (language_hint or "und")
        self.store.assert_actor(context, tenant_data=True)
        self.store.append_audit(
            context=context,
            event="voice.transcribed",
            result="OK",
            resource_scope="voice:stt",
            sensitive_payload={
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
                "transcript_sha256": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
                "duration_seconds": duration_seconds,
                "chars": len(clean),
                "language": language,
            },
        )
        return TranscriptionResult(text=clean, language=language)

    def synthesize_reply(
        self,
        *,
        context: ExecutionContext,
        text: str,
        language: str = "fa-AF",
    ) -> SpeechArtifact:
        if self.tts_provider is None:
            raise VoiceInputDenied("voice synthesis is not configured")
        self.store.assert_actor(context, tenant_data=True)
        clean = text.strip()
        if not clean or len(clean) > self.max_transcript_chars:
            raise VoiceInputDenied("voice synthesis text denied")
        artifact = self.tts_provider.synthesize(text=clean, language=language)
        if artifact.mime_type not in self._OUTPUT_MIMES:
            raise VoiceInputDenied("voice synthesis MIME type denied")
        if not artifact.content or len(artifact.content) > self.max_tts_bytes:
            raise VoiceInputDenied("voice synthesis size denied")
        self.store.assert_actor(context, tenant_data=True)
        self.store.append_audit(
            context=context,
            event="voice.synthesized",
            result="OK",
            resource_scope="voice:tts",
            sensitive_payload={
                "text_sha256": hashlib.sha256(clean.encode("utf-8")).hexdigest(),
                "bytes": len(artifact.content),
                "language": language,
                "mime_type": artifact.mime_type,
            },
        )
        return artifact
