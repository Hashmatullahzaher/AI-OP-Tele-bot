import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from osai.bot.catalog import catalog_from_mapping
from osai.bot.service import BotService, bootstrap_store
from osai.bot.stt import LocalWhisperProvider, RemoteWhisperProvider, _pick_language, stt_from_env
from osai.bot.telegram_poll import TelegramPoller
from osai.channels.telegram import TelegramError
from osai.storage import TenantSecurityStore
from osai.voice import TranscriptionResult, VoiceProviderUnavailable, VoiceService
from tests.test_bot import CEO_ID, EXAMPLE, TOKEN


class FakeWhisperModel:
    def __init__(self, first_language, probs=None):
        self.first_language = first_language
        self.probs = probs
        self.calls = []

    def transcribe(self, audio, language=None, beam_size=5, vad_filter=True):
        self.calls.append(language)
        info = SimpleNamespace(language=language or self.first_language, all_language_probs=self.probs)
        return iter([SimpleNamespace(text=" مجموع مصارف "), SimpleNamespace(text="چقدر است")]), info


class SttTests(unittest.TestCase):
    def test_pick_language_keeps_supported_and_remaps_others(self):
        self.assertIsNone(_pick_language("fa", None))
        self.assertEqual(_pick_language("ur", [("ur", 0.6), ("ps", 0.3), ("fa", 0.1)]), "ps")
        self.assertEqual(_pick_language("ar", None), "fa")

    def test_local_provider_retranscribes_in_supported_language(self):
        model = FakeWhisperModel("ur", [("ur", 0.6), ("ps", 0.3)])
        result = LocalWhisperProvider(model=model).transcribe(
            audio=b"ogg", mime_type="audio/ogg", language_hint=None, duration_seconds=3
        )
        self.assertEqual(model.calls, [None, "ps"])
        self.assertEqual(result, TranscriptionResult(text="مجموع مصارف چقدر است", language="ps"))

    def test_local_provider_failure_is_unavailable(self):
        class Broken:
            def transcribe(self, *a, **k):
                raise RuntimeError("decoder crashed")

        with self.assertRaises(VoiceProviderUnavailable):
            LocalWhisperProvider(model=Broken()).transcribe(
                audio=b"x", mime_type="audio/ogg", language_hint=None, duration_seconds=1
            )

    def test_remote_provider_sends_multipart_and_maps_language(self):
        seen = {}

        def post(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=body)
            return {"text": " total expenses ", "language": "english"}

        provider = RemoteWhisperProvider(
            base_url="https://api.groq.com/openai/v1", model="whisper-large-v3", api_key="gsk", http_post=post
        )
        result = provider.transcribe(audio=b"OGGDATA", mime_type="audio/ogg", language_hint=None, duration_seconds=2)
        self.assertEqual(result, TranscriptionResult(text="total expenses", language="en"))
        self.assertEqual(seen["url"], "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer gsk")
        self.assertIn(b"OGGDATA", seen["body"])
        self.assertIn(b"whisper-large-v3", seen["body"])

    def test_remote_requires_https(self):
        with self.assertRaises(ValueError):
            RemoteWhisperProvider(base_url="http://example.com/v1", model="m", api_key=None)

    def test_stt_from_env(self):
        self.assertIsInstance(stt_from_env({}), LocalWhisperProvider)
        self.assertIsNone(stt_from_env({"OSAI_STT": "off"}))
        groq = stt_from_env({"OSAI_STT": "groq", "OSAI_STT_API_KEY": "k"})
        self.assertEqual(groq.model, "whisper-large-v3")
        with self.assertRaises(ValueError):
            stt_from_env({"OSAI_STT": "custom"})
        with self.assertRaises(ValueError):
            stt_from_env({"OSAI_STT": "nope"})


class FixedStt:
    def __init__(self, text="total expenses?", language="en"):
        self.result = TranscriptionResult(text=text, language=language)
        self.calls = 0

    def transcribe(self, *, audio, mime_type, language_hint, duration_seconds):
        self.calls += 1
        return self.result


class FakeAgentService(BotService):
    def handle_text(self, *, telegram_user_id, text):
        return f"ANSWER({text})"


class VoiceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenantSecurityStore(Path(self.tmp.name) / "osai.sqlite3")
        self.catalog = catalog_from_mapping(EXAMPLE)
        bootstrap_store(self.store, self.catalog)
        self.downloads = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def download(self, file_id):
        self.downloads.append(file_id)
        return b"OggS-audio"

    def service(self, stt):
        voice = VoiceService(store=self.store, stt_provider=stt) if stt else None
        return FakeAgentService(agent=None, catalog=self.catalog, voice=voice)

    def test_voice_is_transcribed_echoed_and_answered(self):
        reply = self.service(FixedStt("مجموع مصارف چقدر است؟", "fa")).handle_voice(
            telegram_user_id=CEO_ID,
            voice={"file_id": "v1", "duration": 4, "mime_type": "audio/ogg"},
            download=self.download,
        )
        self.assertEqual(self.downloads, ["v1"])
        self.assertIn("🎤 شنیدم: «مجموع مصارف چقدر است؟»", reply)
        self.assertIn("ANSWER(مجموع مصارف چقدر است؟)", reply)

    def test_unknown_user_is_refused_before_download(self):
        reply = self.service(FixedStt()).handle_voice(
            telegram_user_id=42, voice={"file_id": "v1", "duration": 4}, download=self.download
        )
        self.assertIn("42", reply)
        self.assertEqual(self.downloads, [])

    def test_too_long_voice_is_not_downloaded(self):
        reply = self.service(FixedStt()).handle_voice(
            telegram_user_id=CEO_ID, voice={"file_id": "v1", "duration": 600}, download=self.download
        )
        self.assertIn("120", reply)
        self.assertEqual(self.downloads, [])

    def test_voice_disabled(self):
        reply = self.service(None).handle_voice(
            telegram_user_id=CEO_ID, voice={"file_id": "v1", "duration": 4}, download=self.download
        )
        self.assertIn("not enabled", reply)

    def test_provider_outage_is_busy(self):
        class Down:
            def transcribe(self, **kwargs):
                raise VoiceProviderUnavailable("down")

        reply = self.service(Down()).handle_voice(
            telegram_user_id=CEO_ID, voice={"file_id": "v1", "duration": 4}, download=self.download
        )
        self.assertIn("busy", reply)


class FakeTransport:
    def __init__(self, updates):
        self.updates = updates
        self.calls = []

    def post_json(self, url, *, payload, timeout_seconds):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "voice/file_1.oga"}}
        return {"ok": True, "result": {"message_id": 1}}


class FakeMedia:
    def __init__(self):
        self.urls = []

    def get_bytes(self, url, *, timeout_seconds, max_bytes):
        self.urls.append(url)
        return b"OggS"


class PollerVoiceTests(unittest.TestCase):
    def test_voice_update_downloads_through_file_endpoint(self):
        voice = {"file_id": "v1", "duration": 3}
        transport = FakeTransport(
            [{"update_id": 1, "message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 5}, "voice": voice}}]
        )
        media = FakeMedia()

        def on_voice(user_id, v, download):
            return f"got {len(download(v['file_id']))} bytes"

        TelegramPoller(
            token=TOKEN, on_text=lambda *a: "", on_voice=on_voice, transport=transport, media=media
        ).poll_once()
        self.assertEqual(media.urls, [f"https://api.telegram.org/file/bot{TOKEN}/voice/file_1.oga"])
        self.assertEqual([p for m, p in transport.calls if m == "sendMessage"][0]["text"], "got 4 bytes")

    def test_unsafe_file_path_rejected(self):
        transport = FakeTransport([])
        transport.post_json = lambda url, **k: {"ok": True, "result": {"file_path": "../../etc/passwd"}}
        poller = TelegramPoller(token=TOKEN, on_text=lambda *a: "", transport=transport, media=FakeMedia())
        with self.assertRaises(TelegramError):
            poller.download_file("v1")


if __name__ == "__main__":
    unittest.main()
