"""Provider adapters behind one interface: local Ollama (default) and a cloud channel (prepared).

Selection happens on the server from settings (`NBA_AI_PROVIDER`). There is no automatic
fallback from one provider to another: a provider that is off, misconfigured or failing raises,
and the calling artifact shows its deterministic template instead.
"""

import json
import os
import time
import urllib.request
from typing import Protocol
from urllib.parse import urlparse

from app.ai.contracts import GenerationRequest, GenerationResult
from app.core.config import Settings, get_settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ProviderError(RuntimeError):
    """The provider could not produce an answer. The artifact falls back to its template."""


class ProviderNotConfigured(ProviderError):
    """The selected provider is off or incomplete. Never silently replaced by another."""


class DataPolicyError(ProviderError):
    """The request may not be sent to this provider (for example identifiable data to a cloud)."""


class ModelProvider(Protocol):
    name: str
    model: str
    is_local: bool

    def generate(self, request: GenerationRequest) -> GenerationResult: ...


def check_data_policy(
    provider: ModelProvider, request: GenerationRequest, settings: Settings
) -> None:
    """Identifiable data stays on the machine unless the operator explicitly allows otherwise."""
    if (
        request.contains_identifiable_data
        and not provider.is_local
        and not settings.allow_external_identifiable_data
    ):
        raise DataPolicyError("identifiable data may not be sent to a non-local provider")


class OllamaProvider:
    """Local Ollama on this machine. Loopback only, no proxy, no cloud model tags."""

    name = "ollama"
    is_local = True

    def __init__(self, base_url: str, model: str, timeout: float) -> None:
        if urlparse(base_url).hostname not in LOOPBACK:
            raise ProviderNotConfigured(f"Ollama must run on this machine, not {base_url}")
        if not model or "cloud" in model.lower():
            raise ProviderNotConfigured(f"not a local Ollama model: {model!r}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
        )
        with self._opener.open(req, timeout=self.timeout) as response:
            return json.load(response)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        options: dict = {
            "temperature": request.temperature,
            "num_ctx": 8192,
            "num_predict": request.max_output_tokens,
        }
        if request.seed is not None:
            options["seed"] = request.seed
        body = {
            "model": self.model,
            "messages": [m.model_dump() for m in request.messages],
            "format": request.json_schema,
            "stream": False,
            "think": False,
            "options": options,
        }
        start = time.perf_counter()
        try:
            out = self._post("/api/chat", body)
        except Exception as exc:  # noqa: BLE001 - every runtime failure becomes a ProviderError
            raise ProviderError(f"ollama: {type(exc).__name__}") from exc
        return GenerationResult(
            text=out.get("message", {}).get("content", ""),
            provider=self.name,
            model=self.model,
            is_local=True,
            prompt_tokens=out.get("prompt_eval_count"),
            output_tokens=out.get("eval_count"),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


class CloudProvider:
    """Organisation-funded cloud model API. Prepared, not implemented: it never sends anything.

    The vendor adapter (request format, auth header, structured-output support) is written when
    the organisation chooses a vendor and signs the data agreement. Until then `generate` raises,
    so a cloud setting can never cause a call or a charge.
    """

    is_local = False

    def __init__(self, vendor: str, model: str, base_url: str, api_key_env: str) -> None:
        missing = [
            n
            for n, v in (("vendor", vendor), ("model", model), ("api_key_env", api_key_env))
            if not v
        ]
        if missing:
            raise ProviderNotConfigured(f"cloud provider incomplete: set {', '.join(missing)}")
        if not os.environ.get(api_key_env):
            raise ProviderNotConfigured(f"cloud API key variable {api_key_env} is not set")
        self.name = f"cloud:{vendor}"
        self.model = model
        self.base_url = base_url

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderNotConfigured(f"{self.name} adapter is not implemented yet")


def get_provider(settings: Settings | None = None) -> ModelProvider:
    """The one provider the server is configured for. Raises if it is off or incomplete."""
    s = settings or get_settings()
    choice = s.ai_provider.strip().lower()
    if choice == "ollama":
        return OllamaProvider(s.ai_ollama_base_url, s.ai_ollama_model, s.ai_timeout_seconds)
    if choice == "cloud":
        return CloudProvider(
            s.ai_cloud_vendor, s.ai_cloud_model, s.ai_cloud_base_url, s.ai_cloud_api_key_env
        )
    if choice == "off":
        raise ProviderNotConfigured("AI generation is switched off (NBA_AI_PROVIDER=off)")
    raise ProviderNotConfigured(f"unknown AI provider: {s.ai_provider!r}")
