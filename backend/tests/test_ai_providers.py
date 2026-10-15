"""Provider channel for the shared AI layer: local by default, cloud prepared, never silent."""

import socket

import pytest

from app.ai.contracts import GenerationMessage, GenerationRequest
from app.ai.providers import (
    CloudProvider,
    DataPolicyError,
    OllamaProvider,
    ProviderError,
    ProviderNotConfigured,
    check_data_policy,
    get_provider,
)
from app.core.config import Settings


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _request(identifiable: bool = False) -> GenerationRequest:
    return GenerationRequest(
        artifact="case_brief",
        prompt_version="4",
        messages=[GenerationMessage(role="user", content="hello")],
        json_schema={"type": "object"},
        contains_identifiable_data=identifiable,
    )


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_default_is_the_local_ollama_model():
    provider = get_provider(_settings())
    assert isinstance(provider, OllamaProvider)
    assert provider.is_local and provider.base_url == "http://127.0.0.1:11434"
    assert provider.model == "qwen3.5:9b-q4_K_M"


@pytest.mark.parametrize(
    ("url", "model"),
    [
        ("https://ollama.com", "qwen3.5:9b-q4_K_M"),
        ("http://10.0.0.5:11434", "qwen3.5:9b-q4_K_M"),
        ("http://127.0.0.1:11434", "gemma4:31b-cloud"),
        ("http://127.0.0.1:11434", ""),
    ],
)
def test_ollama_refuses_remote_hosts_and_cloud_tags(url, model):
    with pytest.raises(ProviderNotConfigured):
        get_provider(_settings(ai_ollama_base_url=url, ai_ollama_model=model))


def test_off_and_unknown_providers_raise_instead_of_falling_back():
    for choice in ("off", "openai", ""):
        with pytest.raises(ProviderNotConfigured):
            get_provider(_settings(ai_provider=choice))


def test_cloud_needs_vendor_model_and_a_key_variable(monkeypatch):
    monkeypatch.delenv("NBA_TEST_CLOUD_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured):
        get_provider(_settings(ai_provider="cloud"))
    configured = _settings(
        ai_provider="cloud",
        ai_cloud_vendor="example",
        ai_cloud_model="example-model",
        ai_cloud_api_key_env="NBA_TEST_CLOUD_KEY",
    )
    with pytest.raises(ProviderNotConfigured, match="NBA_TEST_CLOUD_KEY"):
        get_provider(configured)


def test_cloud_provider_never_calls_out(monkeypatch, no_network):
    monkeypatch.setenv("NBA_TEST_CLOUD_KEY", "placeholder-not-a-real-key")
    provider = get_provider(
        _settings(
            ai_provider="cloud",
            ai_cloud_vendor="example",
            ai_cloud_model="example-model",
            ai_cloud_api_key_env="NBA_TEST_CLOUD_KEY",
        )
    )
    assert isinstance(provider, CloudProvider) and not provider.is_local
    with pytest.raises(ProviderNotConfigured):
        provider.generate(_request())


def test_identifiable_data_stays_local_unless_the_operator_allows_it(monkeypatch):
    monkeypatch.setenv("NBA_TEST_CLOUD_KEY", "placeholder-not-a-real-key")
    cloud = CloudProvider("example", "example-model", "", "NBA_TEST_CLOUD_KEY")
    local = OllamaProvider("http://127.0.0.1:11434", "qwen3.5:9b-q4_K_M", 5)
    strict, allowed = _settings(), _settings(allow_external_identifiable_data=True)
    with pytest.raises(DataPolicyError):
        check_data_policy(cloud, _request(identifiable=True), strict)
    check_data_policy(cloud, _request(identifiable=False), strict)
    check_data_policy(cloud, _request(identifiable=True), allowed)
    check_data_policy(local, _request(identifiable=True), strict)


def test_ollama_request_carries_the_schema_and_returns_untrusted_text(monkeypatch):
    provider = OllamaProvider("http://127.0.0.1:11434", "qwen3.5:9b-q4_K_M", 5)
    sent = {}

    def fake_post(path, body):
        sent.update(path=path, body=body)
        return {"message": {"content": '{"ok": true}'}, "prompt_eval_count": 3, "eval_count": 4}

    monkeypatch.setattr(provider, "_post", fake_post)
    result = provider.generate(_request())
    assert sent["path"] == "/api/chat"
    assert sent["body"]["format"] == {"type": "object"}
    assert sent["body"]["think"] is False and sent["body"]["stream"] is False
    assert result.text == '{"ok": true}' and result.is_local and result.output_tokens == 4


def test_ollama_failure_is_a_provider_error_not_a_crash(monkeypatch):
    provider = OllamaProvider("http://127.0.0.1:11434", "qwen3.5:9b-q4_K_M", 5)

    def broken(path, body):
        raise OSError("connection refused")

    monkeypatch.setattr(provider, "_post", broken)
    with pytest.raises(ProviderError):
        provider.generate(_request())
