"""Provider-neutral voice-note processing boundary for OS AI Core F7.

The channel must authenticate/pair the user first and supply a trusted
ExecutionContext. This module re-checks the tenant actor, applies audio/text
limits, delegates to provider adapters, and persists only redacted audit
metadata. It does not persist raw audio or transcripts.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from .contracts import ExecutionContext, PolicyDenied
from .storage import TenantSecurityStore


class VoiceError(RuntimeError):
    """Base sanitized voice error."""


class VoiceAccessDenied(VoiceError):
    """The authenticated actor may not use the voice operation."""


class VoiceInvalid(VoiceError):
    """Audio/text input violates a bounded voice contract."""


class VoiceProviderUnavailable(VoiceError):
    """The configured speech provider is unavailable."""


class SpeechToTextProvider(Protocol):
    provider_name: str

    def transcribe(
        self,
        *,
        audio: bytes,
        content_type: str,
        language_hint: str | None,
        correlation_id: str,
    ) -> tuple[str, str | None]:
        """Return transcript and detected language without trusted tenant context."""


class TextToSpeechProvider(Protocol):
    provider_name: str

    def synthesize(
        self,
        *,
        text: str,
        language: str | None,
        correlation_id: str,
    ) -> tuple[bytes, str]:
        """Return bounded audio bytes and MIME type."""


@dataclass(frozen=True)
class VoiceTranscript:
    text: str
    detected_language: str | None
    provider_name: str
    audio_sha256: str
    correlation_id: str


@dataclass(frozen=True)
class SynthesizedSpeech:
    audio: bytes
    content_type: str
    provider_name: str
    text_sha256: str
    correlation_id: str


class VoiceProcessor:
    """Apply the same tenant identity/audit boundary to STT and optional TTS."""

    _ALLOWED_AUDIO = frozenset({"audio/ogg", "audio/opus", "audio/mpeg", "audio/wav"})

    def __init__(
        self,
        *,
        tenant_store: TenantSecurityStore,
        stt_provider: SpeechToTextProvider,
        tts_provider: TextToSpeechProvider | None = None,
        max_audio_bytes: int = 20_000_000,
        max_duration_seconds: int = 300,
        max_transcript_chars: int = 12_000,
        max_tts_chars: int = 4_000,
        max_tts_bytes: int = 20_000_000,
    ) -> None:
        if max_audio_bytes < 1 or max_audio_bytes > 50_000_000:
            raise ValueError("max_audio_bytes out of range")
        if max_duration_seconds < 1 or max_duration_seconds > 900:
            raise ValueError("max_duration_seconds out of range")
        if max_transcript_chars < 1 or max_transcript_chars > 32_000:
            raise ValueError("max_transcript_chars out of range")
        if max_tts_chars < 1 or max_tts_chars > 8_000:
            raise ValueError("max_tts_chars out of range")
        if max_tts_bytes < 1 or max_tts_bytes > 50_000_000:
            raise ValueError("max_tts_bytes out of range")
        self.tenant_store = tenant_store
        self.stt_provider = stt_provider
        self.tts_provider = tts_provider
        self.max_audio_bytes = max_audio_bytes
        self.max_duration_seconds = max_duration_seconds
        self.max_transcript_chars = max_transcript_chars
        self.max_tts_chars = max_tts_chars
        self.max_tts_bytes = max_tts_bytes

    def transcribe(
        self,
        *,
        context: ExecutionContext,
        audio: bytes,
        content_type: str,
        duration_seconds: int,
        language_hint: str | None = None,
    ) -> VoiceTranscript:
        self._authorize(context)
        if content_type not in self._ALLOWED_AUDIO:
            raise VoiceInvalid("voice content type is unsupported")
        if not audio or len(audio) > self.max_audio_bytes:
            raise VoiceInvalid("voice payload size is invalid")
        if duration_seconds < 1 or duration_seconds > self.max_duration_seconds:
            raise VoiceInvalid("voice duration is invalid")
        if language_hint is not None and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,8})?", language_hint):
            raise VoiceInvalid("voice language hint is invalid")
        digest = hashlib.sha256(audio).hexdigest()
        try:
            text, detected = self.stt_provider.transcribe(
                audio=audio,
                content_type=content_type,
                language_hint=language_hint,
                correlation_id=context.correlation_id,
            )
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceProviderUnavailable("speech-to-text provider unavailable") from exc
        clean = text.strip()
        if not clean or len(clean) > self.max_transcript_chars:
            raise VoiceInvalid("speech transcript is empty or exceeds limit")
        if detected is not None and len(detected) > 32:
            raise VoiceInvalid("detected language is invalid")
        self.tenant_store.append_audit(
            context=context,
            event="voice.transcribed",
            result="OK",
            resource_scope="voice:stt",
            sensitive_payload={
                "provider": self.stt_provider.provider_name,
                "audio_sha256": digest,
                "duration_seconds": duration_seconds,
                "transcript_chars": len(clean),
                "detected_language": detected,
            },
        )
        return VoiceTranscript(
            text=clean,
            detected_language=detected,
            provider_name=self.stt_provider.provider_name,
            audio_sha256=digest,
            correlation_id=context.correlation_id,
        )

    def synthesize(
        self,
        *,
        context: ExecutionContext,
        text: str,
        language: str | None = None,
    ) -> SynthesizedSpeech:
        self._authorize(context)
        if self.tts_provider is None:
            raise VoiceProviderUnavailable("text-to-speech provider is not configured")
        clean = text.strip()
        if not clean or len(clean) > self.max_tts_chars:
            raise VoiceInvalid("speech text is empty or exceeds limit")
        if language is not None and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,8})?", language):
            raise VoiceInvalid("speech language is invalid")
        digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        try:
            audio, content_type = self.tts_provider.synthesize(
                text=clean,
                language=language,
                correlation_id=context.correlation_id,
            )
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceProviderUnavailable("text-to-speech provider unavailable") from exc
        if content_type not in self._ALLOWED_AUDIO or not audio or len(audio) > self.max_tts_bytes:
            raise VoiceInvalid("text-to-speech provider returned invalid audio")
        self.tenant_store.append_audit(
            context=context,
            event="voice.synthesized",
            result="OK",
            resource_scope="voice:tts",
            sensitive_payload={
                "provider": self.tts_provider.provider_name,
                "text_sha256": digest,
                "text_chars": len(clean),
                "content_type": content_type,
                "audio_bytes": len(audio),
            },
        )
        return SynthesizedSpeech(
            audio=audio,
            content_type=content_type,
            provider_name=self.tts_provider.provider_name,
            text_sha256=digest,
            correlation_id=context.correlation_id,
        )

    def _authorize(self, context: ExecutionContext) -> None:
        try:
            self.tenant_store.assert_actor(context, tenant_data=True)
        except PolicyDenied as exc:
            raise VoiceAccessDenied("voice operation unavailable") from exc
