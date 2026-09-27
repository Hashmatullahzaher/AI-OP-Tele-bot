"""Speech-to-text providers for Telegram voice notes.

- ``local``: open-source Whisper via ``faster-whisper`` on our own server. It is
  free and audio never leaves the server.
- ``groq`` / ``openai`` / ``custom``: any OpenAI-compatible
  ``/audio/transcriptions`` API (e.g. Groq ``whisper-large-v3``).

Both implement :class:`osai.voice.SpeechToTextProvider`.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..voice import SpeechToTextProvider, TranscriptionResult, VoiceInputDenied, VoiceProviderUnavailable

# Whisper language codes we serve: Dari uses the Persian model ("fa").
SUPPORTED_LANGUAGES = ("fa", "ps", "en")


def _pick_language(detected: str, probabilities: Iterable[tuple[str, float]] | None) -> str | None:
    """Keep Whisper's guess if supported, else the most likely supported language."""

    if detected in SUPPORTED_LANGUAGES:
        return None
    best: tuple[float, str] | None = None
    for code, prob in probabilities or ():
        if code in SUPPORTED_LANGUAGES and (best is None or prob > best[0]):
            best = (prob, code)
    return best[1] if best else "fa"


class LocalWhisperProvider:
    """faster-whisper on CPU. ``small`` fits a 4 GB server; ``medium`` is more accurate."""

    def __init__(self, *, model_size: str = "small", model: Any = None, download_root: str | None = None) -> None:
        self.model_size = model_size
        self.download_root = download_root
        self._model = model
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:  # pragma: no cover - depends on install
                raise VoiceProviderUnavailable("install 'faster-whisper' (requirements-voice.txt)") from exc
            self._model = WhisperModel(
                self.model_size, device="cpu", compute_type="int8", download_root=self.download_root
            )
        return self._model

    def _run(self, audio: bytes, language: str | None) -> tuple[str, Any]:
        segments, info = self._get_model().transcribe(
            io.BytesIO(audio), language=language, beam_size=5, vad_filter=True
        )
        return " ".join(segment.text.strip() for segment in segments).strip(), info

    def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        language_hint: str | None,
        duration_seconds: int,
    ) -> TranscriptionResult:
        with self._lock:  # one transcription at a time on a small CPU server
            try:
                text, info = self._run(audio, None)
                retry = _pick_language(str(info.language), getattr(info, "all_language_probs", None))
                if retry is not None:
                    text, info = self._run(audio, retry)
                    language = retry
                else:
                    language = str(info.language)
            except VoiceProviderUnavailable:
                raise
            except Exception as exc:
                raise VoiceProviderUnavailable("local speech-to-text failed") from exc
        if not text:
            raise VoiceInputDenied("no speech recognized")
        return TranscriptionResult(text=text, language=language)


HttpPost = Callable[[str, Mapping[str, str], bytes, int], Mapping[str, Any]]


def _post(url: str, headers: Mapping[str, str], body: bytes, timeout: int) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL from operator config
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        raise VoiceProviderUnavailable(f"speech-to-text API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VoiceProviderUnavailable("speech-to-text API unreachable") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceProviderUnavailable("speech-to-text API returned invalid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise VoiceProviderUnavailable("speech-to-text API returned an unexpected response")
    return decoded


_LANGUAGE_NAMES = {"persian": "fa", "farsi": "fa", "pashto": "ps", "english": "en"}


class RemoteWhisperProvider:
    """OpenAI-compatible ``/audio/transcriptions`` (Groq, OpenAI, self-hosted)."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: int = 60,
        http_post: HttpPost = _post,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
            raise ValueError("speech-to-text URL must be https (http only for localhost)")
        if not model:
            raise ValueError("speech-to-text model is required")
        self.url = base_url.rstrip("/") + "/audio/transcriptions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.http_post = http_post

    def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        language_hint: str | None,
        duration_seconds: int,
    ) -> TranscriptionResult:
        boundary = uuid.uuid4().hex
        fields = {"model": self.model, "response_format": "verbose_json", "temperature": "0"}
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        ]
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n".encode()
            + audio
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self.http_post(self.url, headers, b"".join(parts), self.timeout_seconds)
        text = response.get("text")
        if not isinstance(text, str) or not text.strip():
            raise VoiceInputDenied("no speech recognized")
        raw_language = str(response.get("language") or "").strip().lower()
        language = _LANGUAGE_NAMES.get(raw_language, raw_language or "und")
        return TranscriptionResult(text=text.strip(), language=language)


_REMOTE_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "whisper-large-v3"),
    "openai": ("https://api.openai.com/v1", "whisper-1"),
}


def stt_from_env(env: Mapping[str, str], *, data_dir: str | None = None) -> SpeechToTextProvider | None:
    """Build the configured provider; ``OSAI_STT=off`` disables voice notes."""

    mode = env.get("OSAI_STT", "local").strip().lower() or "local"
    if mode == "off":
        return None
    if mode == "local":
        return LocalWhisperProvider(
            model_size=env.get("OSAI_STT_MODEL", "").strip() or "small",
            download_root=f"{data_dir}/whisper-models" if data_dir else None,
        )
    if mode in _REMOTE_PRESETS or mode == "custom":
        default_url, default_model = _REMOTE_PRESETS.get(mode, ("", ""))
        base_url = env.get("OSAI_STT_BASE_URL", "").strip() or default_url
        if not base_url:
            raise ValueError("OSAI_STT_BASE_URL is required for OSAI_STT=custom")
        return RemoteWhisperProvider(
            base_url=base_url,
            model=env.get("OSAI_STT_MODEL", "").strip() or default_model,
            api_key=env.get("OSAI_STT_API_KEY", "").strip() or None,
        )
    raise ValueError("OSAI_STT must be local, groq, openai, custom or off")
