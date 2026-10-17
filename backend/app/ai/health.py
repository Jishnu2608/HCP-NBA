"""Provider availability, reported as what is actually known.

`provider_status(settings)` says how the provider is configured without touching the network:
`disabled` (NBA_AI_PROVIDER=off), `misconfigured` (cloud requested, unknown provider, a
non-loopback URL or a cloud model tag) or `configured_unprobed`. Only `probe=True` makes one
loopback request (`GET /api/tags`, 5 seconds) and answers `available` (the configured model is
installed), `model_missing` or `unavailable`. A healthy provider is never inferred from the
absence of failed generations.
"""

import json
import time
import urllib.request

from app.ai import netguard
from app.ai.providers import OllamaProvider, ProviderNotConfigured, get_provider
from app.core.config import Settings, get_settings

PROBE_TIMEOUT = 5.0


def provider_status(settings: Settings | None = None, *, probe: bool = False) -> dict:
    s = settings or get_settings()
    out: dict = {
        "provider": s.ai_provider.strip().lower(),
        "model": s.ai_ollama_model,
        "local_only": True,
        "probed": False,
    }
    try:
        provider = get_provider(s)
    except ProviderNotConfigured as exc:
        off = out["provider"] == "off"
        out["state"] = "disabled" if off else "misconfigured"
        out["reason"] = "switched_off" if off else type(exc).__name__.lower()
        return out
    if not probe or not isinstance(provider, OllamaProvider):
        out["state"] = "configured_unprobed"
        return out
    out["probed"] = True
    started = time.perf_counter()
    try:
        netguard.assert_loopback(provider.base_url)
        req = urllib.request.Request(provider.base_url + "/api/tags")
        with netguard.opener().open(req, timeout=PROBE_TIMEOUT) as response:
            tags = json.load(response)
    except Exception as exc:  # noqa: BLE001 - any failure means not available; no detail kept
        out.update(state="unavailable", reason=type(exc).__name__.lower())
        return out
    out["latency_ms"] = int((time.perf_counter() - started) * 1000)
    names = {m.get("name") for m in tags.get("models", [])} | {
        m.get("model") for m in tags.get("models", [])
    }
    out["state"] = "available" if provider.model in names else "model_missing"
    return out
