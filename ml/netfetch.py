"""Скачивание больших файлов с повторами: сборка и первый запуск у проверяющего не должны падать из-за одного обрыва.

Только стандартная библиотека: tenacity и подобных в образе migrate нет.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

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
            e.close()  # иначе соединение остаётся открытым
            last = e
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
