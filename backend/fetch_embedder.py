"""Модель эмбеддингов для образа worker-ml: скачивается при сборке (стадия deps-ml в backend/Dockerfile).

    python backend/fetch_embedder.py /opt/models/multilingual-e5-small

Ревизия на Hugging Face закреплена, каждый файл сверяется с хешем из этой ревизии: крупные (LFS) — по sha256,
мелкие — по git blob sha1. Берутся только файлы, нужные sentence-transformers (без onnx, openvino и копии весов
в pytorch_model.bin). Во время работы воркер в сеть за моделью не ходит (HF_HUB_OFFLINE=1 в образе).
Только стандартная библиотека: скрипт запускается до установки зависимостей.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

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


def digest(kind: str, data: bytes) -> str:
    if kind == "blob":
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 — так git адресует объекты
    return hashlib.sha256(data).hexdigest()


def fetch(dest: Path) -> None:
    for name, (kind, expected) in FILES.items():
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        with urllib.request.urlopen(url, timeout=600) as response:
            data = response.read()
        if (got := digest(kind, data)) != expected:
            raise SystemExit(f"{name}: хеш не совпал (ожидали {expected}, получили {got})")
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"{name}: {len(data)} байт", file=sys.stderr)


if __name__ == "__main__":
    fetch(Path(sys.argv[1]))
