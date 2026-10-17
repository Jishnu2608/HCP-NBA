"""Application-level network controls of the local provider, exercised on the real HTTP path.

A stub HTTP server on 127.0.0.1 stands in for Ollama; a socket watcher records every address
this process connects to while the provider runs. These tests prove what this application's
own requests do. They do not prove operating-system egress isolation (the Ollama server is a
separate process): that needs a host firewall rule, documented in docs/ai-architecture.md.
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.ai import health, netguard
from app.ai.contracts import GenerationMessage, GenerationRequest
from app.ai.providers import OllamaProvider, ProviderError, ProviderNotConfigured, get_provider
from app.core.config import Settings

MODEL = "qwen3.5:9b-q4_K_M"


class Stub(BaseHTTPRequestHandler):
    redirect_to: str | None = None
    models = [{"name": MODEL, "digest": "56671c2ab938deadbeef"}]

    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: dict, headers: dict | None = None):
        data = json.dumps(body).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.redirect_to:
            return self._send(302, {}, {"Location": self.redirect_to})
        self._send(200, {"models": self.models})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.redirect_to:
            return self._send(307, {}, {"Location": self.redirect_to})
        self._send(
            200, {"message": {"content": '{"ok": true}'}, "prompt_eval_count": 3, "eval_count": 2}
        )


@pytest.fixture
def stub():
    server = HTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Stub.redirect_to = None
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def watcher(monkeypatch):
    """Every address a socket of this process connects to."""
    seen: list[str] = []
    original = socket.socket.connect

    def connect(self, address):
        seen.append(address[0] if isinstance(address, tuple) else str(address))
        return original(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    return seen


def _request() -> GenerationRequest:
    return GenerationRequest(
        artifact="test",
        prompt_version="1",
        messages=[GenerationMessage(role="user", content="hello")],
        json_schema={"type": "object"},
    )


def test_the_real_provider_path_connects_to_loopback_only(stub, watcher):
    provider = OllamaProvider(stub, MODEL, timeout=5)
    result = provider.generate(_request())
    assert result.text == '{"ok": true}' and result.is_local
    assert provider.digest() == "56671c2ab938"
    assert watcher and all(addr in ("127.0.0.1", "::1") for addr in watcher)


def test_a_redirect_is_never_followed(stub, watcher):
    Stub.redirect_to = "http://192.0.2.10:11434/api/chat"  # TEST-NET-1: never routable
    provider = OllamaProvider(stub, MODEL, timeout=5)
    with pytest.raises(ProviderError):
        provider.generate(_request())
    assert provider.digest() is None
    assert all(addr in ("127.0.0.1", "::1") for addr in watcher), watcher


def test_a_name_that_resolves_off_the_machine_is_refused(monkeypatch, watcher):
    def fake_getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.20", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(netguard.NonLocalAddress):
        netguard.assert_loopback("http://localhost:11434")
    provider = OllamaProvider("http://localhost:11434", MODEL, timeout=5)
    with pytest.raises(ProviderError):
        provider.generate(_request())
    assert watcher == []  # refused before any connection


@pytest.mark.parametrize(
    "url", ["ftp://127.0.0.1/", "http:///nohost", "http://10.0.0.5:11434", "http://192.0.2.1:11434"]
)
def test_non_loopback_or_malformed_urls_are_refused(url):
    with pytest.raises((netguard.NonLocalAddress, ProviderNotConfigured)):
        netguard.assert_loopback(url)
        OllamaProvider(url, MODEL, timeout=5)


def test_loopback_addresses_are_accepted():
    netguard.assert_loopback("http://127.0.0.1:11434")
    netguard.assert_loopback("http://localhost:11434")


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_provider_status_is_known_only_after_an_explicit_probe(stub, watcher):
    s = _settings(ai_provider="ollama", ai_ollama_base_url=stub, ai_ollama_model=MODEL)
    assert health.provider_status(s)["state"] == "configured_unprobed"
    assert watcher == []  # configuration only: no network
    assert health.provider_status(s, probe=True)["state"] == "available"
    Stub.models = [{"name": "other:7b"}]
    try:
        assert health.provider_status(s, probe=True)["state"] == "model_missing"
    finally:
        Stub.models = [{"name": MODEL, "digest": "56671c2ab938deadbeef"}]


def test_provider_status_reports_off_misconfigured_and_unavailable():
    assert health.provider_status(_settings(ai_provider="off"))["state"] == "disabled"
    assert health.provider_status(_settings(ai_provider="cloud"))["state"] == "misconfigured"
    remote = _settings(ai_provider="ollama", ai_ollama_base_url="http://10.1.2.3:11434")
    assert health.provider_status(remote)["state"] == "misconfigured"
    closed = _settings(ai_provider="ollama", ai_ollama_base_url="http://127.0.0.1:9")
    assert health.provider_status(closed, probe=True)["state"] == "unavailable"


def test_cloud_is_never_selected_whatever_is_configured():
    with pytest.raises(ProviderNotConfigured):
        get_provider(
            _settings(
                ai_provider="cloud",
                ai_cloud_vendor="x",
                ai_cloud_model="y",
                ai_cloud_api_key_env="PATH",
            )
        )
