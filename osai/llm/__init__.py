"""Provider-neutral LLM connections for the OS AI Core planner.

Any OpenAI-compatible chat API (OpenRouter, OpenAI, Groq, Gemini, DeepSeek,
Mistral, Together, Azure, Ollama, LM Studio, vLLM, ...) and the native
Anthropic Messages API are supported. Credentials may be an API key, a custom
header, no authentication (local models) or a short-lived sign-in token
fetched by a command (for example ``gcloud auth print-access-token``).
"""

from .auth import CommandTokenCredential, Credential, NoCredential, StaticCredential
from .config import PRESETS, LLMSettings, settings_from_env
from .planner import (
    AnthropicPlanner,
    OpenAICompatiblePlanner,
    TableDescription,
    build_planner,
    parse_plan_text,
)

__all__ = [
    "PRESETS",
    "AnthropicPlanner",
    "CommandTokenCredential",
    "Credential",
    "LLMSettings",
    "NoCredential",
    "OpenAICompatiblePlanner",
    "StaticCredential",
    "TableDescription",
    "build_planner",
    "parse_plan_text",
    "settings_from_env",
]
