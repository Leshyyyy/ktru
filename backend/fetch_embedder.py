"""Модель эмбеддингов для образа worker-ml: скачивается при сборке (стадия deps-ml в backend/Dockerfile).

    python backend/fetch_embedder.py /opt/models/multilingual-e5-small

Ревизия на Hugging Face закреплена, каждый файл сверяется с хешем из этой ревизии: крупные (LFS) — по sha256,
мелкие — по git blob sha1. Берутся только файлы, нужные sentence-transformers (без onnx, openvino и копии весов
в pytorch_model.bin). Во время работы воркер в сеть за моделью не ходит (HF_HUB_OFFLINE=1 в образе).
Только стандартная библиотека: скрипт запускается до установки зависимостей.

Файлы качаются потоком (модель ~470 МБ не держится в памяти целиком) с повторами на 408/425/429/5xx, обрывах и
таймаутах: один сбой сети иначе роняет сборку образа у проверяющего. `download` — копия `ml/netfetch.py`: Dockerfile
копирует в стадию сборки только этот файл, импортировать из ml/ нельзя.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://huggingface.co"

REPO = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
# путь → ("sha256", хеш LFS-объекта) или ("blob", git blob sha1)
FILES = {
    "config.json": ("blob", "60a2a84020f1d74cc53ea9e8c4e91cf4af6c2b68"),
    "modules.json": ("blob", "ac2039abdf6ff023b27c919bb9675cfe378cb10f"),
    "sentence_bert_config.json": ("blob", "4eca68d85ecd3034cf4174d8a4033a75344ea62d"),
    "1_Pooling/config.json": ("blob", "acbd4014202bf4dbc6c03229bcbeef7c4feafb58"),
    "special_tokens_map.json": ("blob", "e0b1d18ecd0ae4ff1d47bd297d910c0cf83e504b"),
    "tokenizer_config.json": ("blob", "059214673d9d6d2ee319411e2ffec8c024b816d5"),
    "tokenizer.json": ("sha256", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
    "sentencepiece.bpe.model": ("sha256", "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"),
    "model.safetensors": ("sha256", "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"),
}


RETRY_HTTP = {408, 425, 429, 500, 502, 503, 504}
CHUNK = 1 << 20


class DownloadError(RuntimeError):
    """Файл не получен: ошибка не проходит сама или кончились попытки. Текст — для человека."""


def _fetch(url: str, part: Path, timeout: float) -> str:
    """Потоком в part, сверка с Content-Length. → sha256 полученного."""
    req = urllib.request.Request(url, headers={"User-Agent": "ktru-fetch/1.0"})  # noqa: S310 — адрес задаёт вызывающий код
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:  # noqa: S310
        expected = resp.headers.get("Content-Length")
        while chunk := resp.read(CHUNK):
            out.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if expected is not None and size != int(expected):
        raise http.client.IncompleteRead(b"", int(expected) - size)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    *,
    sha256: str | None = None,
    attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    timeout: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Атомарно записывает файл в dest. Повторяет 408/425/429/5xx, обрывы и таймауты с паузой base_delay·2ⁿ и джиттером.

    Другие 4xx и несовпадение sha256 сами не пройдут: DownloadError сразу.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)  # noqa: S311 — не криптография
        try:
            got = _fetch(url, part, timeout)
            if sha256 and got != sha256:
                part.unlink(missing_ok=True)
                raise DownloadError(f"sha256 не совпал: ожидали {sha256}, получили {got}. Файл в источнике изменился.")
            os.replace(part, dest)
            return
        except urllib.error.HTTPError as e:
            last = e
            e.close()  # ответ с ошибкой держит соединение открытым
            if e.code not in RETRY_HTTP:
                part.unlink(missing_ok=True)
                raise DownloadError(f"HTTP {e.code} для {url}") from e
            retry_after = e.headers.get("Retry-After") if e.headers else None
            if retry_after and retry_after.isdigit():
                delay = min(max_delay, float(retry_after))
        except (TimeoutError, http.client.HTTPException, OSError) as e:  # URLError и обрывы соединения — это OSError
            last = e
        part.unlink(missing_ok=True)
        if attempt < attempts:
            log.warning("Загрузка не удалась (%s), попытка %d/%d, пауза %.1f с", last, attempt, attempts, delay)
            sleep(delay)
    raise DownloadError(f"Не удалось скачать {url} за {attempts} попыток: {last}") from last


def blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 — так git адресует объекты


def fetch(dest: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
    for name, (kind, expected) in FILES.items():
        path = dest / name
        download(
            f"{BASE_URL}/{REPO}/resolve/{REVISION}/{name}",
            path,
            sha256=expected if kind == "sha256" else None,
            sleep=sleep,
        )
        # Мелкие файлы git хранит как blob: их хеш считается по содержимому целиком, после записи.
        if kind == "blob" and (got := blob_sha1(path.read_bytes())) != expected:
            path.unlink()
            raise DownloadError(f"{name}: хеш не совпал (ожидали {expected}, получили {got})")
        print(f"{name}: {path.stat().st_size} байт", file=sys.stderr)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        fetch(Path(sys.argv[1]))
    except DownloadError as e:
        raise SystemExit(str(e)) from e
