import json
import unittest
from types import SimpleNamespace

from osai.agent import AgentPlanInvalid, ProviderUnavailable, ToolCatalogEntry
from osai.llm import (
    AnthropicPlanner,
    CommandTokenCredential,
    NoCredential,
    OpenAICompatiblePlanner,
    StaticCredential,
    TableDescription,
    parse_plan_text,
    settings_from_env,
)
from osai.llm.config import LLMConfigError

PLAN = {
    "schema_version": "0.1",
    "action": "tool",
    "capability": "drive.table.read",
    "arguments": {"resource_alias": "finance", "table_alias": "expenses"},
    "analysis": {"kind": "sum", "column": "Amount", "filters": []},
}
CATALOG = (
    ToolCatalogEntry(
        capability="drive.table.read",
        connector_type="drive",
        action="READ",
        input_schema={"type": "object"},
    ),
)
TABLES = (TableDescription("finance", "expenses", "Company expenses", ("Month", "Amount")),)


class SettingsTests(unittest.TestCase):
    def test_openrouter_is_default_and_needs_model(self):
        with self.assertRaises(LLMConfigError):
            settings_from_env({"OSAI_LLM_API_KEY": "sk-or-abc"})
        s = settings_from_env({"OSAI_LLM_API_KEY": "sk-or-abc", "OSAI_LLM_MODEL": "meta/llama:free"})
        self.assertEqual(s.protocol, "openai_compatible")
        self.assertEqual(s.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(s.credential.headers(), {"Authorization": "Bearer sk-or-abc"})

    def test_presets_cover_other_providers(self):
        for preset, host in [
            ("openai", "api.openai.com"),
            ("groq", "api.groq.com"),
            ("gemini", "generativelanguage.googleapis.com"),
            ("deepseek", "api.deepseek.com"),
        ]:
            s = settings_from_env({"OSAI_LLM_PRESET": preset, "OSAI_LLM_API_KEY": "k", "OSAI_LLM_MODEL": "m"})
            self.assertIn(host, s.base_url)

    def test_anthropic_preset_has_default_model(self):
        s = settings_from_env({"OSAI_LLM_PRESET": "anthropic", "OSAI_LLM_API_KEY": "k"})
        self.assertEqual(s.protocol, "anthropic")
        self.assertEqual(s.model, "claude-opus-5")

    def test_local_model_needs_no_key(self):
        s = settings_from_env({"OSAI_LLM_PRESET": "ollama", "OSAI_LLM_MODEL": "qwen2.5:7b"})
        self.assertIsInstance(s.credential, NoCredential)
        self.assertFalse(s.json_mode)

    def test_custom_endpoint_with_custom_header(self):
        s = settings_from_env(
            {
                "OSAI_LLM_PRESET": "custom",
                "OSAI_LLM_BASE_URL": "https://my-azure.openai.azure.com/openai/v1/",
                "OSAI_LLM_MODEL": "gpt",
                "OSAI_LLM_AUTH": "header",
                "OSAI_LLM_AUTH_HEADER": "api-key",
                "OSAI_LLM_API_KEY": "secret",
                "OSAI_LLM_EXTRA_HEADERS": "X-Env=prod",
            }
        )
        self.assertEqual(s.base_url, "https://my-azure.openai.azure.com/openai/v1")
        self.assertEqual(s.credential.headers(), {"api-key": "secret"})
        self.assertEqual(s.extra_headers["X-Env"], "prod")

    def test_custom_requires_base_url(self):
        with self.assertRaises(LLMConfigError):
            settings_from_env({"OSAI_LLM_PRESET": "custom", "OSAI_LLM_MODEL": "m", "OSAI_LLM_API_KEY": "k"})

    def test_plain_http_only_for_local_servers(self):
        with self.assertRaises(LLMConfigError):
            settings_from_env(
                {
                    "OSAI_LLM_PRESET": "custom",
                    "OSAI_LLM_BASE_URL": "http://example.com/v1",
                    "OSAI_LLM_MODEL": "m",
                    "OSAI_LLM_AUTH": "none",
                }
            )

    def test_url_with_embedded_credentials_rejected(self):
        with self.assertRaises(LLMConfigError):
            settings_from_env(
                {
                    "OSAI_LLM_PRESET": "custom",
                    "OSAI_LLM_BASE_URL": "https://user:pw@example.com/v1",
                    "OSAI_LLM_MODEL": "m",
                    "OSAI_LLM_AUTH": "none",
                }
            )

    def test_missing_key_is_a_config_error_without_secret(self):
        with self.assertRaises(LLMConfigError) as ctx:
            settings_from_env({"OSAI_LLM_MODEL": "m"})
        self.assertIn("credential", str(ctx.exception))

    def test_unknown_preset_rejected(self):
        with self.assertRaises(LLMConfigError):
            settings_from_env({"OSAI_LLM_PRESET": "nope"})

    def test_static_credential_repr_is_redacted(self):
        self.assertNotIn("topsecret", repr(StaticCredential("topsecret")))


class CommandTokenTests(unittest.TestCase):
    def test_token_is_cached_and_refreshed(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return f"token-{len(calls)}\n"

        cred = CommandTokenCredential(command="gcloud auth print-access-token", runner=runner)
        self.assertEqual(cred.headers(), {"Authorization": "Bearer token-1"})
        self.assertEqual(cred.headers(), {"Authorization": "Bearer token-1"})
        self.assertEqual(calls, [["gcloud", "auth", "print-access-token"]])
        cred._expires_at = 0
        self.assertEqual(cred.secret(), "token-2")

    def test_command_settings_from_env(self):
        s = settings_from_env(
            {
                "OSAI_LLM_PRESET": "custom",
                "OSAI_LLM_BASE_URL": "https://aiplatform.googleapis.com/v1/openapi",
                "OSAI_LLM_MODEL": "google/gemini",
                "OSAI_LLM_AUTH": "command",
                "OSAI_LLM_TOKEN_COMMAND": "gcloud auth print-access-token",
            }
        )
        self.assertIsInstance(s.credential, CommandTokenCredential)


class ParseTests(unittest.TestCase):
    def test_parses_fenced_json(self):
        self.assertEqual(parse_plan_text("```json\n" + json.dumps(PLAN) + "\n```"), PLAN)

    def test_rejects_non_json(self):
        with self.assertRaises(AgentPlanInvalid):
            parse_plan_text("I think the answer is 42")


def openrouter_settings(**extra):
    env = {"OSAI_LLM_API_KEY": "sk-or-abc", "OSAI_LLM_MODEL": "meta/llama:free"}
    env.update(extra)
    return settings_from_env(env)


class OpenAICompatibleTests(unittest.TestCase):
    def test_request_shape_and_plan(self):
        seen = {}

        def post(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return {"choices": [{"message": {"content": json.dumps(PLAN)}}]}

        planner = OpenAICompatiblePlanner(openrouter_settings(), TABLES, http_post=post)
        plan = planner.plan(user_message="مجموع مصارف؟", tool_catalog=CATALOG, correlation_id="c")
        self.assertEqual(plan, PLAN)
        self.assertEqual(seen["url"], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-or-abc")
        self.assertEqual(seen["body"]["model"], "meta/llama:free")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        system = seen["body"]["messages"][0]["content"]
        self.assertIn("expenses", system)
        self.assertIn("Amount", system)
        self.assertEqual(seen["body"]["messages"][1], {"role": "user", "content": "مجموع مصارف؟"})

    def test_provider_error_is_unavailable(self):
        planner = OpenAICompatiblePlanner(
            openrouter_settings(), TABLES, http_post=lambda *a: {"error": {"message": "rate limited"}}
        )
        with self.assertRaises(ProviderUnavailable):
            planner.plan(user_message="q", tool_catalog=CATALOG, correlation_id="c")

    def test_missing_choices_is_unavailable(self):
        planner = OpenAICompatiblePlanner(openrouter_settings(), TABLES, http_post=lambda *a: {"choices": []})
        with self.assertRaises(ProviderUnavailable):
            planner.plan(user_message="q", tool_catalog=CATALOG, correlation_id="c")


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class AnthropicTests(unittest.TestCase):
    def settings(self):
        return settings_from_env({"OSAI_LLM_PRESET": "anthropic", "OSAI_LLM_API_KEY": "k"})

    def test_text_blocks_are_parsed(self):
        response = SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=json.dumps(PLAN)),
            ],
        )
        messages = FakeMessages(response)
        planner = AnthropicPlanner(self.settings(), TABLES, client=SimpleNamespace(messages=messages))
        self.assertEqual(planner.plan(user_message="q", tool_catalog=CATALOG, correlation_id="c"), PLAN)
        self.assertEqual(messages.kwargs["model"], "claude-opus-5")
        self.assertIn("TABLES", messages.kwargs["system"])

    def test_refusal_is_unavailable(self):
        response = SimpleNamespace(stop_reason="refusal", content=[])
        planner = AnthropicPlanner(self.settings(), TABLES, client=SimpleNamespace(messages=FakeMessages(response)))
        with self.assertRaises(ProviderUnavailable):
            planner.plan(user_message="q", tool_catalog=CATALOG, correlation_id="c")


if __name__ == "__main__":
    unittest.main()
