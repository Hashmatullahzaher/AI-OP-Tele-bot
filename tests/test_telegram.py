import json
import tempfile
import unittest
from pathlib import Path

from osai.channels.telegram import (
    StaticBotTokenProvider,
    StaticWebhookSecretProvider,
    TelegramAccessDenied,
    TelegramIngress,
    TelegramSender,
)
from osai.storage import TenantSecurityStore


BOT_ALIAS = "pilot-bot"
WEBHOOK_SECRET = "webhook_secret_123"
BOT_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcd"


class FakeSecuritySink:
    def __init__(self):
        self.events = []

    def emit(self, *, event, metadata):
        self.events.append((event, dict(metadata)))


class FakeTelegramTransport:
    def __init__(self, response=None):
        self.response = response or {"ok": True, "result": {"message_id": 99}}
        self.calls = []

    def post_json(self, url, *, payload, timeout_seconds):
        self.calls.append((url, dict(payload), timeout_seconds))
        return self.response


def update(
    update_id,
    *,
    text="hello",
    chat_type="private",
    chat_id=42,
    user_id=42,
    message_id=7,
):
    return json.dumps(
        {
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "from": {"id": user_id, "is_bot": False, "first_name": "Pilot"},
                "chat": {"id": chat_id, "type": chat_type},
                "text": text,
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def headers(secret=WEBHOOK_SECRET):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


class TelegramChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenantSecurityStore(Path(self.tmp.name) / "security.sqlite3")
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "owner")
        self.sink = FakeSecuritySink()
        self.ingress = TelegramIngress(
            store=self.store,
            secret_provider=StaticWebhookSecretProvider(WEBHOOK_SECRET),
            security_sink=self.sink,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def pair(self, *, update_id=1, chat_id=42, user_id=42, now=1000):
        code = self.store.create_telegram_pairing_challenge(
            tenant_id="alpha",
            actor_id="owner",
            bot_alias=BOT_ALIAS,
            now_epoch=now,
        )
        result = self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=update(
                update_id,
                text=f"/pair {code}",
                chat_id=chat_id,
                user_id=user_id,
            ),
            now_epoch=now + 1,
        )
        return result, code

    def test_pair_then_route_private_text_to_trusted_actor(self):
        paired, _ = self.pair()
        self.assertEqual(paired.kind, "paired")
        self.assertEqual(paired.context.tenant_id, "alpha")
        self.assertEqual(paired.context.actor_id, "owner")
        self.assertEqual(paired.context.session_assurance, "telegram-paired")

        message = self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=update(2, text="مصارف پروژه را نشان بده"),
            now_epoch=1002,
        )
        self.assertEqual(message.kind, "message")
        self.assertEqual(message.text, "مصارف پروژه را نشان بده")
        self.assertEqual(message.context.tenant_id, "alpha")
        events = [record.event for record in self.store.audit_records("alpha")]
        self.assertEqual(events, ["telegram.paired", "telegram.message.accepted"])

    def test_pairing_code_is_not_stored_in_plaintext_and_is_single_use(self):
        paired, code = self.pair()
        row = self.store._db.execute(
            "SELECT challenge_hash FROM telegram_pairing_challenges LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["challenge_hash"], code)
        self.assertNotIn(code, str(row["challenge_hash"]))
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(3, text=f"/pair {code}", chat_id=43, user_id=43),
                now_epoch=1003,
            )
        self.assertEqual(paired.chat_id, 42)

    def test_expired_pairing_code_is_denied(self):
        code = self.store.create_telegram_pairing_challenge(
            tenant_id="alpha",
            actor_id="owner",
            bot_alias=BOT_ALIAS,
            now_epoch=1000,
            ttl_seconds=60,
        )
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(1, text=f"/pair {code}"),
                now_epoch=1061,
            )

    def test_invalid_webhook_secret_does_not_claim_update(self):
        self.pair(update_id=1)
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers("wrong_secret"),
                body=update(2, text="hello"),
                now_epoch=1002,
            )
        accepted = self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=update(2, text="hello"),
            now_epoch=1003,
        )
        self.assertEqual(accepted.kind, "message")

    def test_replayed_update_is_denied(self):
        self.pair(update_id=1)
        self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=update(2, text="hello"),
            now_epoch=1002,
        )
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(2, text="hello again"),
                now_epoch=1003,
            )
        self.assertIn("telegram.replay_denied", [event for event, _ in self.sink.events])

    def test_group_and_non_text_updates_are_denied(self):
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(1, chat_type="group", chat_id=-100),
                now_epoch=1000,
            )
        raw = json.dumps(
            {
                "update_id": 2,
                "message": {
                    "message_id": 8,
                    "from": {"id": 42},
                    "chat": {"id": 42, "type": "private"},
                    "voice": {"file_id": "voice-test"},
                },
            }
        ).encode()
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=raw,
                now_epoch=1001,
            )

    def test_unpaired_private_chat_is_denied(self):
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(1, text="show accounts"),
                now_epoch=1000,
            )
        self.assertIn("telegram.unpaired_denied", [event for event, _ in self.sink.events])

    def test_actor_or_binding_revocation_blocks_future_messages(self):
        self.pair(update_id=1)
        self.store.revoke_telegram_binding(
            tenant_id="alpha",
            bot_alias=BOT_ALIAS,
            chat_id=42,
        )
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(2, text="show accounts"),
                now_epoch=1002,
            )

    def test_revoked_actor_blocks_existing_binding(self):
        self.pair(update_id=1)
        self.store.revoke_actor("alpha", "owner")
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=update(2, text="show accounts"),
                now_epoch=1002,
            )

    def test_duplicate_json_fields_fail_closed(self):
        body = (
            b'{"update_id":1,"update_id":2,"message":{"message_id":7,'
            b'"from":{"id":42},"chat":{"id":42,"type":"private"},"text":"hi"}}'
        )
        with self.assertRaises(TelegramAccessDenied):
            self.ingress.handle(
                bot_alias=BOT_ALIAS,
                headers=headers(),
                body=body,
                now_epoch=1000,
            )

    def test_security_logs_never_receive_message_text_or_pairing_code(self):
        _, code = self.pair()
        self.ingress.handle(
            bot_alias=BOT_ALIAS,
            headers=headers(),
            body=update(2, text="secret business question"),
            now_epoch=1002,
        )
        rendered = repr(self.sink.events)
        self.assertNotIn("secret business question", rendered)
        self.assertNotIn(code, rendered)

    def test_sender_rechecks_active_binding_and_context(self):
        paired, _ = self.pair()
        transport = FakeTelegramTransport()
        sender = TelegramSender(
            store=self.store,
            token_provider=StaticBotTokenProvider(BOT_TOKEN),
            transport=transport,
        )
        message_id = sender.send_text(
            context=paired.context,
            bot_alias=BOT_ALIAS,
            chat_id=42,
            telegram_user_id=42,
            text="Report ready",
        )
        self.assertEqual(message_id, 99)
        self.assertEqual(transport.calls[0][1]["chat_id"], 42)
        self.assertEqual(transport.calls[0][1]["text"], "Report ready")

        self.store.revoke_telegram_binding(
            tenant_id="alpha",
            bot_alias=BOT_ALIAS,
            chat_id=42,
        )
        with self.assertRaises(TelegramAccessDenied):
            sender.send_text(
                context=paired.context,
                bot_alias=BOT_ALIAS,
                chat_id=42,
                telegram_user_id=42,
                text="Should not send",
            )
        self.assertEqual(len(transport.calls), 1)

    def test_sender_denies_context_mismatch_and_invalid_bot_token(self):
        paired, _ = self.pair()
        wrong_context = paired.context.__class__.issue(
            tenant_id="alpha",
            actor_id="another",
            session_assurance="telegram-paired",
        )
        sender = TelegramSender(
            store=self.store,
            token_provider=StaticBotTokenProvider(BOT_TOKEN),
            transport=FakeTelegramTransport(),
        )
        with self.assertRaises(TelegramAccessDenied):
            sender.send_text(
                context=wrong_context,
                bot_alias=BOT_ALIAS,
                chat_id=42,
                telegram_user_id=42,
                text="No",
            )

        bad_sender = TelegramSender(
            store=self.store,
            token_provider=StaticBotTokenProvider("not-a-token"),
            transport=FakeTelegramTransport(),
        )
        with self.assertRaises(TelegramAccessDenied):
            bad_sender.send_text(
                context=paired.context,
                bot_alias=BOT_ALIAS,
                chat_id=42,
                telegram_user_id=42,
                text="No",
            )


if __name__ == "__main__":
    unittest.main()
