from __future__ import annotations

import base64
import importlib
import socket
import sys
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
reader = importlib.import_module("reader.haochen_reader")


def dns_result(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://example.com/image.png",
        "ftp://example.com/image.png",
        "https://user:pass@example.com/image.png",
        "https://localhost/image.png",
        "https://example.local/image.png",
        "https://example.com:8443/image.png",
    ],
)
def test_remote_image_url_rejects_unsafe_structure(monkeypatch, url: str) -> None:
    monkeypatch.setattr(reader.socket, "getaddrinfo", lambda *_args, **_kwargs: dns_result("93.184.216.34"))
    with pytest.raises(ValueError):
        reader._validate_remote_image_url(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_remote_image_url_rejects_non_global_dns(monkeypatch, address: str) -> None:
    monkeypatch.setattr(reader.socket, "getaddrinfo", lambda *_args, **_kwargs: dns_result(address))
    with pytest.raises(ValueError):
        reader._validate_remote_image_url("https://images.example.com/photo.png")


def test_remote_image_url_accepts_https_with_only_global_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        reader.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: dns_result("93.184.216.34") + dns_result("2606:2800:220:1:248:1893:25c8:1946"),
    )
    assert reader._validate_remote_image_url("https://images.example.com/photo.png") == (
        "https://images.example.com/photo.png"
    )


class FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/png", content_length: int | None = None):
        self.data = data
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.data[:limit]


def test_download_accepts_small_valid_image(monkeypatch) -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    monkeypatch.setattr(reader, "_secure_urlopen", lambda _url: FakeResponse(png, "image/png", len(png)))
    result = reader._download_as_data_url("https://images.example.com/photo.png")
    assert result == f"data:image/png;base64,{base64.b64encode(png).decode()}"


def test_download_rejects_non_image_mime_before_decoding(monkeypatch) -> None:
    monkeypatch.setattr(reader, "_secure_urlopen", lambda _url: FakeResponse(b"not an image", "text/plain"))
    assert reader._download_as_data_url("https://images.example.com/photo.png") is None


def test_download_rejects_declared_or_actual_oversize(monkeypatch) -> None:
    limit = reader.MAX_REMOTE_IMAGE_BYTES
    monkeypatch.setattr(
        reader,
        "_secure_urlopen",
        lambda _url: FakeResponse(b"x", content_length=limit + 1),
    )
    assert reader._download_as_data_url("https://images.example.com/photo.png") is None

    monkeypatch.setattr(
        reader,
        "_secure_urlopen",
        lambda _url: FakeResponse(b"x" * (limit + 1)),
    )
    assert reader._download_as_data_url("https://images.example.com/photo.png") is None


@pytest.mark.parametrize("declared", [-1, "invalid"])
def test_download_rejects_invalid_content_length(monkeypatch, declared) -> None:
    response = FakeResponse(b"x")
    response.headers["Content-Length"] = str(declared)
    monkeypatch.setattr(reader, "_secure_urlopen", lambda _url: response)
    assert reader._download_as_data_url("https://images.example.com/photo.png") is None


def test_redirect_handler_revalidates_destination(monkeypatch) -> None:
    monkeypatch.setattr(reader.socket, "getaddrinfo", lambda *_args, **_kwargs: dns_result("127.0.0.1"))
    handler = reader._SafeRedirectHandler()
    with pytest.raises(ValueError):
        handler.redirect_request(
            SimpleNamespace(full_url="https://example.com"),
            None,
            302,
            "Found",
            {},
            "https://localhost/private",
        )
