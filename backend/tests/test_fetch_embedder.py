"""Скачивание модели для образа worker-ml: повторы, потоковая запись, сверка хешей. Сеть — локальный HTTP-сервер."""

from __future__ import annotations

import hashlib
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend import fetch_embedder
from backend.fetch_embedder import DownloadError, blob_sha1, download

BODY = b"x" * 3_000_000
SHA = hashlib.sha256(BODY).hexdigest()


class Server:
    """Отвечает по сценарию: шаг на запрос, последний шаг повторяется. Шаги: ok, 503, 404, cut (обрыв посреди файла)."""

    def __init__(self, script: list[str], body: bytes = BODY) -> None:
        self.script, self.body, self.requests = script, body, 0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_GET(self) -> None:
                step = server.script[min(server.requests, len(server.script) - 1)]
                server.requests += 1
                if step in {"503", "404"}:
                    self.send_response(int(step))
                    self.send_header("Retry-After", "1")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                if step == "cut":  # заявлен весь файл, отдана треть — и соединение закрыто
                    self.wfile.write(server.body[: len(server.body) // 3])
                    self.close_connection = True
                else:
                    self.wfile.write(server.body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


@pytest.fixture
def serve() -> Iterator:
    servers: list[Server] = []

    def start(script: list[str], body: bytes = BODY) -> Server:
        servers.append(Server(script, body))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def test_recovers_after_503_and_cut(serve, tmp_path: Path) -> None:
    server = serve(["503", "cut", "ok"])
    pauses: list[float] = []
    download(f"{server.url}/f", tmp_path / "f.bin", sha256=SHA, sleep=pauses.append)
    assert server.requests == 3
    assert (tmp_path / "f.bin").read_bytes() == BODY
    assert pauses[0] == 1.0  # Retry-After
    assert len(pauses) == 2
    assert not (tmp_path / "f.bin.part").exists()


def test_404_not_retried(serve, tmp_path: Path) -> None:
    server = serve(["404"])
    with pytest.raises(DownloadError, match="HTTP 404"):
        download(f"{server.url}/f", tmp_path / "f", sleep=lambda _: None)
    assert server.requests == 1


def test_bad_sha_not_retried_and_no_file(serve, tmp_path: Path) -> None:
    server = serve(["ok"])
    with pytest.raises(DownloadError, match="sha256"):
        download(f"{server.url}/f", tmp_path / "f", sha256="0" * 64, sleep=lambda _: None)
    assert server.requests == 1
    assert list(tmp_path.iterdir()) == []


def test_gives_up_after_attempts(serve, tmp_path: Path) -> None:
    server = serve(["503"])
    with pytest.raises(DownloadError, match="5 попыток"):
        download(f"{server.url}/f", tmp_path / "f", sleep=lambda _: None)
    assert server.requests == 5
    assert list(tmp_path.iterdir()) == []


def test_fetch_checks_blob_and_lfs_hashes(serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Мелкий файл сверяется по git blob sha1, крупный — по sha256; несовпадение blob удаляет файл."""
    small = b'{"a": 1}'
    server = serve(["ok"], body=small)
    monkeypatch.setattr(fetch_embedder, "BASE_URL", server.url)
    monkeypatch.setattr(fetch_embedder, "FILES", {"cfg/config.json": ("blob", blob_sha1(small))})
    fetch_embedder.fetch(tmp_path, sleep=lambda _: None)
    assert (tmp_path / "cfg" / "config.json").read_bytes() == small

    monkeypatch.setattr(fetch_embedder, "FILES", {"other.json": ("blob", "0" * 40)})
    with pytest.raises(DownloadError, match=r"other\.json: хеш не совпал"):
        fetch_embedder.fetch(tmp_path, sleep=lambda _: None)
    assert not (tmp_path / "other.json").exists()

    monkeypatch.setattr(fetch_embedder, "FILES", {"model.safetensors": ("sha256", hashlib.sha256(small).hexdigest())})
    fetch_embedder.fetch(tmp_path, sleep=lambda _: None)
    assert (tmp_path / "model.safetensors").read_bytes() == small


def test_pinned_hashes_unchanged() -> None:
    """Ретраи не должны ослабить закрепление: ревизия и хеш весов модели те же."""
    assert fetch_embedder.REVISION == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert fetch_embedder.FILES["model.safetensors"] == (
        "sha256",
        "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477",
    )
