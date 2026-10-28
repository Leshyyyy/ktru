"""«Серебряная» разметка точного кода КТРУ для живых строк: сильная языковая модель выбирает позиции.

    python -m ml.silver --data data/ktru/ste/ste_eval_tair.jsonl --catalog data/ktru/v2/catalog_fetched.jsonl \
        --model google/gemini-3.8-flash --out data/ktru/ste/silver_tair.jsonl

Вход — карточки СТЕ (ml.ste_eval): реальная строка поставщика с характеристиками и ОКПД2, который он поставил.
Модель видит строку и ВСЕ действующие позиции КТРУ этого ОКПД2 с обязательными характеристиками — не наших
кандидатов, поэтому разметка не зависит от поиска конвейера. Ответ — все позиции, которым строка соответствует,
и уверенность; первая — expected_ktru_code, остальные — alt_valid_codes. Неуверенные и пустые ответы
отбрасываются, ОКПД2 с одной позицией размечаются без модели.

Это не ручная разметка: модель может ошибаться, а при той же модели в конвейере (RERANKER=llm) метрика
завышается. Модель для разметки — другого производителя, чем LLM_MODEL конвейера. Нужен LLM_API_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ARTIFACTS, Settings

MAX_POSITIONS = 40  # в ОКПД2 больше позиций — список не помещается в разумный запрос, такие строки пропускаем

PROMPT = (
    "Ты эксперт по госзакупкам и каталогу КТРУ. Дана карточка товара поставщика (наименование и характеристики) "
    "и все позиции КТРУ её кода ОКПД2 с обязательными характеристиками. Определи, каким позициям товар "
    "соответствует: значения характеристик карточки должны попадать в значения позиции. Если для выбора не "
    "хватает данных, перечисли все подходящие. Ответ — только JSON: "
    '{"positions": [номера, лучшая первой], "confidence": "high" | "medium" | "low"}; '
    'если ни одна не подходит — {"positions": [], "confidence": "high"}.'
)


def _position_text(row: dict) -> str:
    chars = [f"{c['name']}: {'; '.join(c['values'][:6])}" + (f" ({c['unit']})" if c.get("unit") not in (None, "-") else "")
             for c in row["characteristics"] if c.get("required")]
    return row["name"] + (" — " + " | ".join(chars) if chars else "")


def main() -> None:
    import httpx  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Серебряная разметка точного кода КТРУ сильной LLM")
    ap.add_argument("--data", required=True, nargs="+")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--cached-only", action="store_true", help="только ответы из кэша, без новых запросов (баланс)")
    args = ap.parse_args()
    settings = Settings()
    if not settings.llm_api_key:
        raise SystemExit("Нужен LLM_API_KEY (ml/.env)")

    by_okpd: dict[str, list[dict]] = defaultdict(list)
    for line in open(args.catalog, encoding="utf-8"):
        row = json.loads(line)
        if row["status"] == "active" and not row.get("is_enlarged"):
            by_okpd[row["okpd2_code"]].append(row)
    samples = [json.loads(line) for path in args.data for line in open(path, encoding="utf-8") if line.strip()]

    url = (settings.llm_base_url or "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    client = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    cache_path = ARTIFACTS / "silver_cache.jsonl"
    cache, lock = {}, threading.Lock()
    if cache_path.exists():
        cache = {r["key"]: r["answer"] for r in map(json.loads, cache_path.read_text(encoding="utf-8").splitlines())}

    def label(s: dict) -> dict | None:
        okpd = s["expected_ktru_code"].split("-")[0]
        rows = sorted(by_okpd.get(okpd, []), key=lambda r: r["ktru_code"])
        if not rows or len(rows) > MAX_POSITIONS:
            return None
        if len(rows) == 1:
            return {**s, "expected_ktru_code": rows[0]["ktru_code"], "label": "single"}
        text = s.get("raw_text_spec") or s["raw_text"]
        options = "\n".join(f"{i}. {_position_text(r)}" for i, r in enumerate(rows, start=1))
        body = {"model": args.model, "temperature": 0, "max_tokens": 1200,
                "messages": [{"role": "system", "content": PROMPT},
                             {"role": "user", "content": f"Карточка: {text[:1500]}\n\nПозиции КТРУ ({okpd}):\n{options}"}]}
        key = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        answer = cache.get(key)
        if answer is None and args.cached_only:
            return None
        if answer is None:
            try:
                for attempt in range(6):  # 429 — лимит частоты у провайдера: ждём и повторяем
                    r = client.post(url, json=body)
                    if r.status_code != 429:
                        break
                    time.sleep(2 ** attempt)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                answer = json.loads(content[content.index("{"):content.rindex("}") + 1])
            except Exception as e:  # noqa: BLE001
                print(f"{s['id']}: {e}; ответ: {r.text[:300] if 'r' in locals() else ''}", file=sys.stderr)
                return None
            with lock:
                cache[key] = answer
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "answer": answer}, ensure_ascii=False) + "\n")
        picked = [rows[i - 1]["ktru_code"] for i in answer.get("positions", []) if isinstance(i, int) and 1 <= i <= len(rows)]
        if not picked or answer.get("confidence") != "high":
            return None
        return {**s, "expected_ktru_code": picked[0], "alt_valid_codes": picked[1:], "label": args.model}

    with ThreadPoolExecutor(args.workers) as ex:
        out = [r for r in ex.map(label, samples) if r]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    single = sum(r["label"] == "single" for r in out)
    print(f"{len(out)} из {len(samples)} размечено (одна позиция в ОКПД2 — {single}, моделью — {len(out) - single}) → {args.out}")


if __name__ == "__main__":
    main()
