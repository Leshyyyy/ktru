"""
StubPipeline — заглушка ML-конвейера для бэкенда, фронтенда и CI.

Возвращает детерминированные ответы (результат зависит только от текста позиции),
имитирует задержку и все три цвета уверенности. Коды здесь ВЫМЫШЛЕННЫЕ (префикс 99.99),
это не настоящие коды КТРУ: заглушка нужна, чтобы разрабатывать очереди, прогресс,
таблицу и экспорт до появления настоящего ML.

Переменные окружения:
  STUB_DELAY_MS   — задержка на позицию, по умолчанию 20 (0 отключает).
  STUB_DEGRADED   — 1: все результаты помечаются mode="degraded" (проверить подсказку в интерфейсе).

Тестовые хуки:
  текст позиции содержит "__fail__" — позиция возвращается с error (проверить статус failed).
"""

from __future__ import annotations

import hashlib
import os
import re
import time

try:  # внутри пакета contracts/
    from .ml_contracts import (
        CharacteristicMatch,
        ClassificationResult,
        ExtractedAttributes,
        PipelineOptions,
        Prediction,
        RawItem,
    )
except ImportError:  # запуск из самой папки contracts/
    from ml_contracts import (  # type: ignore[no-redef]
        CharacteristicMatch,
        ClassificationResult,
        ExtractedAttributes,
        PipelineOptions,
        Prediction,
        RawItem,
    )

FAKE_CATALOG = [
    ("99.99.01.000-00000001", "99.99.01.000", "Ноутбук"),
    ("99.99.01.000-00000002", "99.99.01.000", "Ноутбук игровой"),
    ("99.99.02.000-00000001", "99.99.02.000", "Монитор"),
    ("99.99.03.000-00000001", "99.99.03.000", "Принтер лазерный"),
    ("99.99.03.000-00000002", "99.99.03.000", "Принтер струйный"),
    ("99.99.04.000-00000001", "99.99.04.000", "Бумага офисная"),
    ("99.99.05.000-00000001", "99.99.05.000", "Перчатки медицинские"),
    ("99.99.06.000-00000001", "99.99.06.000", "Шприц одноразовый"),
]

BRANDS = ("Lenovo", "HP", "Dell", "ASUS", "Acer", "Samsung", "Canon", "Xerox", "LG", "Svetocopy")

# Токен из латиницы и цифр, в котором есть и то и другое: 15IAU7, B230, i5-1235U.
_MODEL_RE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\b")


def _h(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def _extract(text: str) -> ExtractedAttributes:
    brand = next((b for b in BRANDS if re.search(rf"\b{re.escape(b)}\b", text, re.I)), None)
    m = _MODEL_RE.search(text)
    head = text.split(",")[0]
    if brand:
        head = re.split(rf"\b{re.escape(brand)}\b", head, flags=re.I)[0]
    name = head.strip() or text.strip()
    return ExtractedAttributes(
        name=name[:120],
        brand=brand,
        model=m.group(0) if m else None,
        method="rules",
        field_confidence={"name": 0.9, "brand": 0.8 if brand else 0.0, "model": 0.7 if m else 0.0},
    )


class StubPipeline:
    def __init__(self) -> None:
        self.delay = int(os.getenv("STUB_DELAY_MS", "20")) / 1000
        self.mode = "degraded" if os.getenv("STUB_DEGRADED") == "1" else "normal"

    def is_ready(self) -> dict[str, bool]:
        return {"models": True, "catalog": True}

    def classify_batch(
        self,
        items: list[RawItem],
        catalog_version_id: str,
        options: PipelineOptions | None = None,
    ) -> list[ClassificationResult]:
        out: list[ClassificationResult] = []
        for it in items:
            t0 = time.perf_counter()
            if self.delay:
                time.sleep(self.delay)

            if "__fail__" in it.text:
                out.append(ClassificationResult(
                    item_id=it.item_id, mode=self.mode,
                    error="Заглушка: имитация сбоя обработки позиции.",
                    timings_ms={"total": round((time.perf_counter() - t0) * 1000, 2)},
                ))
                continue

            h = _h(it.text)
            band = h % 100  # ~60% зелёных, ~25% жёлтых, ~15% красных
            if band < 60:
                top = 0.90 + (h % 9) / 100          # 0.90–0.98
            elif band < 85:
                top = 0.60 + (h % 24) / 100         # 0.60–0.83
            else:
                top = 0.20 + (h % 25) / 100         # 0.20–0.44
            rest = 1.0 - top
            # сумма ≤ 1 и уверенность не растёт с рангом (при top < 0.375 rest*0.6 > top, поэтому min)
            confs = [top, min(rest * 0.6, top * 0.9), min(rest * 0.25, top * 0.45)]

            ext = _extract(it.text)
            norm_key = (f"{ext.name.lower()}|{ext.brand.lower()}|{ext.model.lower()}"
                        if ext.brand and ext.model else None)
            miss = h % 10 == 3  # ~10% позиций: нет обязательной характеристики → предупреждение
            start = h % len(FAKE_CATALOG)
            preds: list[Prediction] = []
            for rank, conf in enumerate(confs, start=1):
                code, okpd2, name = FAKE_CATALOG[(start + rank - 1) % len(FAKE_CATALOG)]
                preds.append(Prediction(
                    rank=rank,
                    ktru_code=code,
                    name=name,
                    okpd2_code=okpd2,
                    confidence=round(conf, 3),
                    features={"top1_fused": round(top, 3)},
                    explanation="Заглушка: результат определён хэшем текста." if rank == 1 else None,
                    why_not_chosen=None if rank == 1 else "Заглушка: оценка ниже, чем у первого кода.",
                    characteristic_matches=(
                        [CharacteristicMatch(name="Тип", expected=name, found=name, status="match", required=True)]
                        + ([CharacteristicMatch(name="Мощность", expected="от 50 Вт", found=None,
                                                status="missing", required=True)] if miss else [])
                        if rank == 1 else []
                    ),
                ))

            out.append(ClassificationResult(
                item_id=it.item_id,
                extracted=ext,
                predictions=preds,
                norm_key=norm_key,
                flags=["required_missing"] if miss else [],
                path="fast" if band < 60 else "rerank",
                mode=self.mode,
                timings_ms={"extract": 1.0, "embed": 1.0, "retrieve": 1.0,
                            "rerank": 0.0 if band < 60 else 1.0, "calibrate": 0.1,
                            "total": round((time.perf_counter() - t0) * 1000, 2)},
            ))
        return out
