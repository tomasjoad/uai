"""Model Router: maps the profiles in model_profiles.yaml to chat models.

Claude goes through ChatAnthropic (the official Anthropic SDK underneath), never
through an OpenAI-compatible shim; local servers (Ollama, LM Studio, vLLM) go
through ChatOpenAI pointed at their base_url. LangChain types stay inside the
orchestrator; nothing outside it sees them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models import BaseChatModel

PUBLIC_FIELDS = ("description", "provider", "model")


class UnknownProfileError(KeyError):
    pass


class ModelRouter:
    def __init__(self, profiles: dict[str, dict[str, Any]], default: str) -> None:
        if default not in profiles:
            raise ValueError(f"Default profile {default!r} is not defined")
        self._profiles = profiles
        self.default = default
        self._models: dict[str, BaseChatModel] = {}

    @classmethod
    def from_file(cls, path: Path) -> ModelRouter:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(data["profiles"], data["default"])

    def has(self, name: str) -> bool:
        return name in self._profiles

    def describe(self) -> list[dict[str, Any]]:
        """Profiles without secrets, for the API."""
        return [
            {"name": name, "default": name == self.default, **{key: profile.get(key) for key in PUBLIC_FIELDS}}
            for name, profile in self._profiles.items()
        ]

    def register(self, name: str, model: BaseChatModel) -> None:
        """Serve a prebuilt model for a profile (tests, or models configured in code)."""
        self._profiles.setdefault(name, {"provider": "custom"})
        self._models[name] = model

    def get(self, name: str) -> BaseChatModel:
        if name not in self._profiles:
            raise UnknownProfileError(name)
        if name not in self._models:
            self._models[name] = _build(self._profiles[name])
        return self._models[name]


def _build(profile: dict[str, Any]) -> BaseChatModel:
    provider = profile.get("provider")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": profile["model"], "max_tokens": profile.get("max_tokens", 16000)}
        if effort := profile.get("effort"):
            kwargs["effort"] = effort
        return ChatAnthropic(**kwargs)
    if provider == "openai_compatible":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=profile["model"],
            base_url=profile["base_url"],
            api_key=profile.get("api_key", "not-needed"),
            max_tokens=profile.get("max_tokens"),
        )
    raise ValueError(f"Unsupported model provider {provider!r}")
