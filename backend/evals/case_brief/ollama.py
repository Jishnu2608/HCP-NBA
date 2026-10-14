"""Minimal local Ollama client. Loopback only, no cloud models, no third-party packages."""

import ctypes
import json
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy


def guard(host: str, model: str | None = None) -> None:
    if urlparse(host).hostname not in LOOPBACK:
        raise ValueError(f"Ollama host must be loopback, got {host}")
    if model and "cloud" in model.lower():
        raise ValueError(f"Cloud model tags are not allowed: {model}")


def _call(host: str, path: str, body: dict | None, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        host + path, data, {"Content-Type": "application/json"} if data else {}
    )
    with _OPENER.open(req, timeout=timeout) as r:
        return json.load(r)


def version(host: str) -> str:
    return _call(host, "/api/version", None, 10)["version"]


def installed(host: str) -> set[str]:
    return {m["name"] for m in _call(host, "/api/tags", None, 10)["models"]}


def loaded(host: str) -> list[dict]:
    return _call(host, "/api/ps", None, 10).get("models", [])


def unload_all(host: str) -> None:
    for m in loaded(host):
        _call(host, "/api/generate", {"model": m["name"], "keep_alive": 0}, 60)


def chat(host: str, model: str, messages: list[dict], schema: dict, timeout: float) -> dict:
    guard(host, model)
    body = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "seed": 7, "num_ctx": 8192, "num_predict": 1200},
        "keep_alive": "10m",
    }
    return _call(host, "/api/chat", body, timeout)


def system_used_gib() -> float | None:
    """Physical memory in use across the machine (Windows only; None elsewhere)."""
    if sys.platform != "win32":
        return None

    class Status(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            ("total", ctypes.c_ulonglong),
            ("avail", ctypes.c_ulonglong),
            ("ptotal", ctypes.c_ulonglong),
            ("pavail", ctypes.c_ulonglong),
            ("vtotal", ctypes.c_ulonglong),
            ("vavail", ctypes.c_ulonglong),
            ("vext", ctypes.c_ulonglong),
        ]

    s = Status()
    s.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return (s.total - s.avail) / 2**30


class PeakMemory:
    """Samples machine memory once a second while a model call runs."""

    def __init__(self) -> None:
        self.peak: float | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            used = system_used_gib()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used
            time.sleep(1)

    def __enter__(self) -> "PeakMemory":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
