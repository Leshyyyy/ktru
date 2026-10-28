"""Проверка запущенного docker compose (CI, job compose): новые эндпоинты на настоящем ML, как у проверяющего.

    python3 backend/tests/compose_smoke.py http://127.0.0.1:8000/api/v1

Только стандартная библиотека: запускается на раннере CI без зависимостей бэкенда. pytest этот файл не собирает.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

IMPORT_TIMEOUT_S = 300
# Маленький каталог для проверки FR-8.1: импорт → строки в базе → индекс в worker-ml → ready.
CATALOG = [
    {"ktru_code": "11.11.11.111-00000001", "okpd2_code": "11.11.11.111", "name": "Стол письменный"},
    {"ktru_code": "11.11.11.111-00000002", "okpd2_code": "11.11.11.111", "name": "Стол", "is_enlarged": True},
]


def call(method: str, url: str, body: bytes | None = None, content_type: str | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 — схема проверена в main()
    if content_type:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — адрес из аргумента CI
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        sys.exit(f"ОШИБКА: {message}\n{json.dumps(payload, ensure_ascii=False, indent=2)[:3000]}")
    print(f"ok: {message}")


def multipart(filename: str, content: bytes, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def main(api: str) -> None:
    text = "Бумага офисная А4, 80 г/м2, 500 листов"
    body = json.dumps({"items": [{"text": text}]}).encode()
    status, data = call("POST", f"{api}/classify", body, "application/json")
    check(status == 200, "POST /classify отвечает 200", data)
    [result] = data["results"]
    check(result["error"] is None and result["predictions"], "POST /classify предлагает код", result)
    check(not result["predictions"][0]["ktru_code"].startswith("99.99."), "коды из настоящего КТРУ", result)

    status, data = call("GET", f"{api}/stats")
    check(status == 200 and "green_correction_rate" in data, "GET /stats", data)
    status, data = call("GET", f"{api}/quality/latest")
    check(status == 200 and isinstance(data, dict), "GET /quality/latest", data)
    status, data = call("GET", f"{api}/catalog/versions")
    check(status == 200 and any(v["status"] == "ready" for v in data["items"]), "есть версия каталога ready", data)

    jsonl = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in CATALOG).encode()
    body, content_type = multipart("ci.jsonl", jsonl, {"note": "CI"})
    status, version = call("POST", f"{api}/catalog/import", body, content_type)
    check(status == 202 and version["status"] == "importing", "POST /catalog/import принят", version)
    deadline = time.monotonic() + IMPORT_TIMEOUT_S
    while time.monotonic() < deadline:
        _, data = call("GET", f"{api}/catalog/versions")
        current = next(v for v in data["items"] if v["version_id"] == version["version_id"])
        if current["status"] != "importing":
            break
        time.sleep(5)
    check(current["status"] == "ready" and current["items_count"] == 2, "новая версия каталога ready", current)


if __name__ == "__main__":
    if not sys.argv[1].startswith(("http://", "https://")):
        sys.exit("Нужен адрес API: http://…/api/v1")
    main(sys.argv[1].rstrip("/"))
