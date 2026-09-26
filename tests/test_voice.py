import tempfile
import unittest
from pathlib import Path

from osai.contracts import ExecutionContext
from osai.storage import TenantSecurityStore
from osai.voice import (
    VoiceAccessDenied,
    VoiceInvalid,
    VoiceProcessor,
    VoiceProviderUnavailable,
)


class FakeSTT:
    provider_name = "fake-stt"

    def __init__(self, text="مصارف پروژه را نشان بده", detected="fa"):
        self.text = text
        self.detected = detected
        self.calls = []

    def transcribe(self, *, audio, content_type, language_hint, correlation_id):
        self.calls.append((audio, content_type, language_hint, correlation_id))
        return self.text, self.detected


class FakeTTS:
    provider_name = "fake-tts"

    def __init__(self, audio=b"voice-output", content_type="audio/ogg"):
        self.audio = audio
        self.content_type = content_type
        self.calls = []

    def synthesize(self, *, text, language, correlation_id):
        self.calls.append((text, language, correlation_id))
        return self.audio, self.content_type


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TenantSecurityStore(Path(self.tmp.name) / "security.sqlite3")
        self.store.add_tenant("alpha")
        self.store.add_actor("alpha", "alice", role="TENANT_ADMIN")
        self.store.add_actor("alpha", "platform", role="PLATFORM_OPERATOR")
        self.context = ExecutionContext.issue(
            tenant_id="alpha",
            actor_id="alice",
            session_assurance="telegram-paired",
        )
        self.stt = FakeSTT()
        self.tts = FakeTTS()
        self.voice = VoiceProcessor(tenant_store=self.store, stt_provider=self.stt, tts_provider=self.tts)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_transcription_uses_trusted_context_but_provider_does_not_receive_tenant_identity(self):
        result = self.voice.transcribe(
            context=self.context,
            audio=b"fake-ogg-bytes",
            content_type="audio/ogg",
            duration_seconds=12,
            language_hint="fa",
        )
        self.assertEqual(result.text, "مصارف پروژه را نشان بده")
        self.assertEqual(result.detected_language, "fa")
        self.assertEqual(len(self.stt.calls[0]), 4)
        rendered = repr(self.stt.calls[0])
        self.assertNotIn("alpha", rendered)
        self.assertNotIn("alice", rendered)

    def test_revoked_actor_has_same_fail_closed_boundary(self):
        self.store.revoke_actor("alpha", "alice")
        with self.assertRaises(VoiceAccessDenied):
            self.voice.transcribe(
                context=self.context,
                audio=b"fake",
                content_type="audio/ogg",
                duration_seconds=2,
            )

    def test_platform_operator_cannot_use_tenant_voice(self):
        context = ExecutionContext.issue(tenant_id="alpha", actor_id="platform")
        with self.assertRaises(VoiceAccessDenied):
            self.voice.transcribe(
                context=context,
                audio=b"fake",
                content_type="audio/ogg",
                duration_seconds=2,
            )

    def test_audio_size_duration_content_type_and_language_are_bounded(self):
        cases = [
            dict(audio=b"", content_type="audio/ogg", duration_seconds=1),
            dict(audio=b"x", content_type="application/octet-stream", duration_seconds=1),
            dict(audio=b"x", content_type="audio/ogg", duration_seconds=301),
            dict(audio=b"x", content_type="audio/ogg", duration_seconds=1, language_hint="bad language"),
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(VoiceInvalid):
                    self.voice.transcribe(context=self.context, **kwargs)

    def test_transcript_is_not_persisted_in_audit(self):
        phrase = "very confidential spoken payroll phrase"
        self.stt.text = phrase
        self.voice.transcribe(
            context=self.context,
            audio=b"confidential-audio",
            content_type="audio/ogg",
            duration_seconds=3,
        )
        dump = "\n".join(self.store._db.iterdump())
        self.assertNotIn(phrase, dump)
        self.assertTrue(self.store.verify_audit_chain("alpha"))
        self.assertEqual(self.store.audit_records("alpha")[-1].event, "voice.transcribed")

    def test_tts_rechecks_identity_and_returns_bounded_audio(self):
        result = self.voice.synthesize(context=self.context, text="Report ready", language="en")
        self.assertEqual(result.audio, b"voice-output")
        self.assertEqual(result.content_type, "audio/ogg")
        self.assertEqual(self.store.audit_records("alpha")[-1].event, "voice.synthesized")

    def test_tts_is_optional_and_invalid_provider_audio_fails_closed(self):
        no_tts = VoiceProcessor(tenant_store=self.store, stt_provider=self.stt)
        with self.assertRaises(VoiceProviderUnavailable):
            no_tts.synthesize(context=self.context, text="hello")
        bad = VoiceProcessor(
            tenant_store=self.store,
            stt_provider=self.stt,
            tts_provider=FakeTTS(audio=b"", content_type="audio/ogg"),
        )
        with self.assertRaises(VoiceInvalid):
            bad.synthesize(context=self.context, text="hello")


if __name__ == "__main__":
    unittest.main()
