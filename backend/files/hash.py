"""Утилиты хеширования файлов и структуры колонок."""

import hashlib
import re
from pathlib import Path
from typing import List, Union


def compute_file_sha256(path: Union[str, Path]) -> str:
    """Вычисляет sha256 хеш содержимого файла."""
    file_path = Path(path)
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_headers_hash(headers: List[str]) -> str:
    """
    Вычисляет устойчивый хеш набора заголовков для сопоставления file_layouts (layout_hint).
    Приводит к нижнему регистру, убирает лишние пробелы и спецсимволы.
    """
    norm_headers = []
    for h in headers:
        cleaned = re.sub(r"\s+", " ", h.strip().lower())
        norm_headers.append(cleaned)
    payload = "\0".join(norm_headers).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
