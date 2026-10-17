"""Application-level network controls for the local model provider.

- `assert_loopback(url)`: the URL's host must resolve, through the system resolver, only to
  loopback addresses. A hosts-file entry or DNS answer that points "localhost" elsewhere is
  refused, not trusted by name.
- `opener()`: a urllib opener with no proxy and no redirect following. A local service that
  answers with a redirect (to any host) gets an error, never a second request.

These controls cover this application's own requests. They do not restrict what other
processes on the machine (including the Ollama server itself) may connect to; that needs an
operating-system firewall rule (see docs/ai-architecture.md, "Network isolation").
"""

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse


class NonLocalAddress(RuntimeError):
    """The provider URL does not resolve to loopback only."""


class RedirectRefused(urllib.error.HTTPError):
    pass


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%")[0]).is_loopback
    except ValueError:
        return False


def assert_loopback(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise NonLocalAddress("no host in provider URL")
    if parsed.scheme not in ("http", "https"):
        raise NonLocalAddress(f"scheme {parsed.scheme!r} not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise NonLocalAddress(f"cannot resolve {host}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_loopback(a) for a in addresses):
        raise NonLocalAddress(f"{host} resolves to a non-loopback address")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        raise RedirectRefused(req.full_url, code, "redirect refused", headers, fp)


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
