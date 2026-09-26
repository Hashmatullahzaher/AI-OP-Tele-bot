import json
import tempfile
import unittest
from pathlib import Path

from osai.channels.telegram import (
    StaticBotTokenProvider,
    StaticWebhookSecretProvider,
    TelegramAccessDenied,
    TelegramIngress,
)
from osai.channels.telegram_voice import (
    TelegramChannelRouter,
    TelegramVoiceIngress,
    TelegramVoiceMediaClient,
)
from osai.contracts import PolicyDenied
from osai.storage import TenantSecurityStore
from osai.voice import SpeechArtifact, TranscriptionResult, VoiceInputDenied, VoiceService

BOT_ALIAS = "pilot-bot"
WEBHOOK_SECRET = "webhook_secret_123"
BOT_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd"


class FakeSecuritySink:
    def __init__(self):
        self.events = []

    def emit(self, *, event, metadata):
        self.events.append((event, dict(metadata)))


class FakeVoiceTransport:
    def __init__(self, *, file_path="voice/file_1.oga", payload=b"OggSvoice"):
        self.file_path = file_path
        self.payload = payload
        self.post_calls = 0
        self.get_calls = 0

    def post_json(self, url, *, payload, timeout_seconds):
        self.post_calls += 1
        return {
            "ok": True,
            "result": {
                "file_id": payload["file_id"],
                "file_path": self.file_path,
                "file_size": len(self.payload),
            },
        }

    def get_bytes(self, url, *, timeout_seconds, max_bytes):
        self.get_calls += 1
        if len(self.payload) > max_bytes:
            raise TelegramAccessDenied("too large")
        return self.payload


class FakeSTT:
    def __init__(self, text="مصارف پروژه را نشان بده", language="fa-AF"):
        self.text = text
        self.language = language
        self.calls = 0

    def transcribe(self, *, audio, mime_type, language_hint, duration_seconds):
        self.calls += 1
        return TranscriptionResult(text=self.text, language=self.language)


class FakeTTS:
    def __init__(self):
        self.calls = 0

    def synthesize(self, *, text, language):
        self.calls += 1
        return SpeechArtifact(content=b"OggSreply", mime_type="audio/ogg")


def headers(secret=WEBHOOK_SECRET):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def text_update(update_id, *, text="hello", chat_id=42, user_id=42):
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": user_id, "is_bot": False},
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        },
        separators=(",", ":"),
    ).encode()


def voice_update(
    update_id,
    *,
    chat_id=42,
    user_id=42,
    chat_type="private",
    duration=5,
    size=1000,
    mime_type="audio/ogg",
):
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {"id": user_id, "is_bot": False},
                "chat": {"id": chat_id, "type": chat_type},
                "voice": {
                    "file_id": "AwACAgQAAx-test-file",
                    "file_unique_id": "unique-test",
                    "duration": duration,
                    "mime_type": mime_type,
                    "file_size": size,
                },
            },
        },
        separators=(",", ":"),
    ).encode()


class VoiceNoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenantSecurityStore(Path(self.tmp.name) / "security.sqlite3")
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "owner")
        self.sink = FakeSecuritySink()
        secret_provider = StaticWebhookSecretProvider(WEBHOOK_SECRET)
        self.text_ingress = TelegramIngress(
            store=self.store,
            secret_provider=secret_provider,
            security_sink=self.sink,
        )
        self.voice_ingress = TelegramVoiceIngress(
            store=self.store,
            secret_provider=secret_provider,
            security_sink=self.sink,
        )
        self.router = TelegramChannelRouter(
            text_ingress=self.text_ingress,
            voice_ingress=self.voice_ingress,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def pair(self, update_id=1):
        code = self.store.create_telegram_pairing_challenge(
            tenant_id="alpha",
            actor_id="owner",
            bot_alias=BOT_ALIAS,
            now_epoch=1000,
        )
        return self.router.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=text_update(update_id, text=f"/pair {code}"),
            now_epoch=1001,
        )

    def test_router_pairs_by_text_then_routes_voice_to_same_actor(self):
        paired = self.pair()
        voice = self.router.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=voice_update(2),
            now_epoch=1002,
        )
        self.assertEqual(paired.context.actor_id, "owner")
        self.assertEqual(voice.kind, "voice")
        self.assertEqual(voice.context.tenant_id, "alpha")
        self.assertEqual(voice.context.actor_id, "owner")
        self.assertEqual(voice.context.session_assurance, "telegram-paired")

    def test_unpaired_voice_is_denied_before_any_media_fetch(self):
        with self.assertRaises(TelegramAccessDenied):
            self.router.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=voice_update(1),
                now_epoch=1000,
            )
        self.assertIn(
            "telegram.unpaired_denied",
            [event for event, _ in self.sink.events],
        )

    def test_group_replay_and_oversized_voice_fail_closed(self):
        with self.assertRaises(TelegramAccessDenied):
            self.router.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=voice_update(1, chat_id=-100, chat_type="group"),
                now_epoch=1000,
            )
        self.pair(update_id=2)
        accepted = voice_update(3)
        self.router.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=accepted,
            now_epoch=1003,
        )
        with self.assertRaises(TelegramAccessDenied):
            self.router.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=accepted,
                now_epoch=1004,
            )
        with self.assertRaises(TelegramAccessDenied):
            self.router.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=voice_update(4, duration=301),
                now_epoch=1005,
            )

    def test_media_fetch_rechecks_binding_and_refuses_path_traversal(self):
        self.pair()
        ingress = self.router.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=voice_update(2),
            now_epoch=1002,
        )
        transport = FakeVoiceTransport()
        client = TelegramVoiceMediaClient(
            store=self.store,
            token_provider=StaticBotTokenProvider(BOT_TOKEN),
            transport=transport,
        )
        payload = client.fetch(bot_alias=BOT_ALIAS, ingress=ingress)
        self.assertEqual(payload, b"OggSvoice")
        self.assertEqual(transport.post_calls, 1)
        self.assertEqual(transport.get_calls, 1)

        bad = FakeVoiceTransport(file_path="../secret.txt")
        bad_client = TelegramVoiceMediaClient(
            store=self.store,
            token_provider=StaticBotTokenProvider(BOT_TOKEN),
            transport=bad,
        )
        with self.assertRaises(TelegramAccessDenied):
            bad_client.fetch(bot_alias=BOT_ALIAS, ingress=ingress)
        self.assertEqual(bad.get_calls, 0)

        self.store.revoke_telegram_binding(
            tenant_id="alpha",
            bot_alias=BOT_ALIAS,
            chat_id=42,
        )
        with self.assertRaises(TelegramAccessDenied):
            client.fetch(bot_alias=BOT_ALIAS, ingress=ingress)
        self.assertEqual(transport.post_calls, 1)

    def test_stt_uses_same_actor_gate_and_never_persists_transcript(self):
        paired = self.pair()
        stt = FakeSTT()
        service = VoiceService(store=self.store, stt_provider=stt)
        result = service.transcribe(
            context=paired.context,
            audio=b"OggSconfidential-audio",
            mime_type="audio/ogg",
            duration_seconds=5,
        )
        self.assertEqual(result.text, "مصارف پروژه را نشان بده")
        self.assertEqual(stt.calls, 1)
        dump = "\n".join(self.store._db.iterdump())
        self.assertNotIn("مصارف پروژه را نشان بده", dump)
        self.assertNotIn("confidential-audio", dump)
        self.assertIn(
            "voice.transcribed",
            [event.event for event in self.store.audit_records("alpha")],
        )

        self.store.revoke_actor("alpha", "owner")
        with self.assertRaises(PolicyDenied):
            service.transcribe(
                context=paired.context,
                audio=b"OggSx",
                mime_type="audio/ogg",
                duration_seconds=2,
            )
        self.assertEqual(stt.calls, 1)

    def test_optional_tts_rechecks_actor_and_is_audited(self):
        paired = self.pair()
        tts = FakeTTS()
        service = VoiceService(
            store=self.store,
            stt_provider=FakeSTT(),
            tts_provider=tts,
        )
        artifact = service.synthesize_reply(
            context=paired.context,
            text="راپور آماده است",
            language="fa-AF",
        )
        self.assertEqual(artifact.mime_type, "audio/ogg")
        self.assertEqual(tts.calls, 1)
        dump = "\n".join(self.store._db.iterdump())
        self.assertNotIn("راپور آماده است", dump)
        self.assertIn(
            "voice.synthesized",
            [event.event for event in self.store.audit_records("alpha")],
        )

    def test_invalid_provider_output_fails_closed(self):
        paired = self.pair()
        service = VoiceService(store=self.store, stt_provider=FakeSTT(text="   "))
        with self.assertRaises(VoiceInputDenied):
            service.transcribe(
                context=paired.context,
                audio=b"OggSx",
                mime_type="audio/ogg",
                duration_seconds=2,
            )


if __name__ == "__main__":
    unittest.main()
