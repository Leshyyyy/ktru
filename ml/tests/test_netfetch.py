"""Повторы при скачивании: локальный сервер отдаёт заданную последовательность ответов."""

import hashlib
import http.server
import threading

import pytest

from ml import fetch_catalog
from ml.netfetch import DownloadError, download

BODY = b"x" * 3_000_000
SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def serve():
    servers = []

    def start(script):
        state = {"n": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                step = script[min(state["n"], len(script) - 1)]
                state["n"] += 1
                if step == "503":
                    self.send_response(503)
                    self.send_header("Retry-After", "7")
                    self.end_headers()
                elif step == "404":
                    self.send_response(404)
                    self.end_headers()
                elif step == "cut":  # заявлено 3 МБ, отдан 1 МБ и обрыв
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY[:1_000_000])
                    self.wfile.flush()
                    self.connection.close()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}/f", state

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_recovers_after_503_and_cut(serve, tmp_path):
    url, state = serve(["503", "cut", "ok"])
    naps = []
    dest = tmp_path / "f.bin"
    download(url, dest, sha256=SHA, sleep=naps.append)
    assert state["n"] == 3
    assert dest.read_bytes() == BODY
    assert len(naps) == 2
    assert naps[0] == 7  # Retry-After важнее своей паузы
    assert not (tmp_path / "f.bin.part").exists()


def test_404_not_retried(serve, tmp_path):
    url, state = serve(["404"])
    with pytest.raises(DownloadError, match="404"):
        download(url, tmp_path / "f", sleep=lambda s: None)
    assert state["n"] == 1


def test_bad_sha_not_retried_and_no_file(serve, tmp_path):
    url, state = serve(["ok"])
    with pytest.raises(DownloadError, match="sha256"):
        download(url, tmp_path / "f", sha256="0" * 64, sleep=lambda s: None)
    assert state["n"] == 1
    assert list(tmp_path.iterdir()) == []


def test_gives_up_after_attempts(serve, tmp_path):
    url, state = serve(["503"])
    with pytest.raises(DownloadError, match="5 попыток"):
        download(url, tmp_path / "f", sleep=lambda s: None)
    assert state["n"] == 5
    assert list(tmp_path.iterdir()) == []


def test_fetch_catalog_stops_with_readable_message(serve, tmp_path, monkeypatch):
    url, _ = serve(["404"])
    monkeypatch.setattr(fetch_catalog, "URL", url)
    with pytest.raises(SystemExit, match="404"):
        fetch_catalog.download(tmp_path / "f", None)
