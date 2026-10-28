"""Ступень 1: категория ОКПД2 по реальным карточкам товаров (СТЕ), а не по тексту КТРУ.

Внутри одного ОКПД2 позиции КТРУ называются одинаково, а между категориями КТРУ по тексту различает плохо: медицинские
разделы с теми же названиями, что у бытовых товаров, случайные совпадения характеристик («Принтер: Да»). Поставщики
СТЕ сами ставят ОКПД2 своим карточкам — это и есть знание о том, что реально покупают. Ближайшие соседи среди названий
карточек дают топ ОКПД2 с долями голосов; позицию КТРУ внутри категорий выбирает уже конвейер (ml/pipeline.py).

    python -m ml.category --ste data/ktru/v2/ste.parquet --catalog data/ktru/v2/catalog_fetched.jsonl \
        --exclude data/ktru/ste/ste_eval_tair.jsonl data/ktru/ste/ste_dev.jsonl --out ml/models/category.joblib

Карточки проверочных наборов (--exclude) и их точные повторы по названию в модель не попадают.
Модель лежит в git и попадает в образ, как калибратор (данные СТЕ разрешены Егором, 28.10).
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

K_NEIGHBORS = 30
VOTE_POWER = 6  # вес соседа — близость в этой степени: решают самые близкие (на ste_dev 2 → 74,6%, 6 → 79,9%)
WORD_SHARE = 0.3  # доля словных признаков рядом с символьными 3–4-граммами (только символы — 78,5%)
CHUNK = 200  # запросов за одно умножение: плотная строка схожести на ~1 млн названий — 4 МБ


def norm_name(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", text))


def query_text(text: str, name_column: str = "") -> str:
    """Что сравнивать с названиями карточек: колонку наименования, иначе текст до первой «;» или перевода строки.
    Характеристики в запросе модели категорий мешают (на ste_dev: 73% по названию против 62% со всей строкой)."""
    if name_column.strip():
        return name_column
    return re.split(r"[;\n]", text, maxsplit=1)[0][:200]


class NameVectorizer:
    """TF-IDF по символьным 3–4-граммам и по словам (1–2), склеенные с весами; строки нормированы.
    Символы ловят формы слов и опечатки, слова — главное существительное и марку целиком."""

    def __init__(self, word_share: float = WORD_SHARE, min_df: int = 3):
        from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415

        self.word_share = word_share
        self.chars = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), sublinear_tf=True, min_df=min_df,
                                     dtype=np.float32)
        self.words = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True,
                                     min_df=max(1, min_df - 1), dtype=np.float32)

    def _join(self, a, b):
        from scipy.sparse import hstack  # noqa: PLC0415
        from sklearn.preprocessing import normalize  # noqa: PLC0415

        return normalize(hstack([a * (1 - self.word_share) ** 0.5, b * self.word_share ** 0.5]).tocsr())

    def fit_transform(self, texts: list[str]):
        return self._join(self.chars.fit_transform(texts), self.words.fit_transform(texts))

    def transform(self, texts: list[str]):
        return self._join(self.chars.transform(texts), self.words.transform(texts))


class CategoryModel:
    """Голосование ближайших соседей: вес соседа — косинусная близость в степени VOTE_POWER."""

    def __init__(self, vectorizer, matrix, labels: np.ndarray, codes: list[str]):
        self.vectorizer = vectorizer
        self.matrix = matrix  # csr, строки нормированы
        self.labels = labels  # индекс кода ОКПД2 для каждой строки
        self.codes = codes

    @classmethod
    def fit(cls, names: list[str], okpd2: list[str]) -> "CategoryModel":
        codes = sorted(set(okpd2))
        index = {c: i for i, c in enumerate(codes)}
        vec = NameVectorizer(min_df=3 if len(names) > 10_000 else 1)
        matrix = vec.fit_transform([norm_name(n) for n in names]).tocsr()
        labels = np.array([index[c] for c in okpd2], dtype=np.int32)
        return cls(vec, matrix, labels, codes)

    def predict_batch(self, texts: list[str], k: int = K_NEIGHBORS) -> list[tuple[dict[str, float], float]]:
        """→ для каждого текста (доли голосов по ОКПД2 по убыванию, близость ближайшего соседа)."""
        out = []
        q = self.vectorizer.transform([norm_name(t) for t in texts])
        n = self.matrix.shape[0]
        k = min(k, n)
        for start in range(0, q.shape[0], CHUNK):
            sims = (q[start:start + CHUNK] @ self.matrix.T).toarray()
            for row in sims:
                top = np.argpartition(-row, k - 1)[:k] if k < n else np.arange(n)
                vote: dict[int, float] = defaultdict(float)
                for j in top:
                    if row[j] > 0:
                        vote[int(self.labels[j])] += float(row[j]) ** VOTE_POWER
                total = sum(vote.values())
                if not total:
                    out.append(({}, 0.0))
                    continue
                probs = {self.codes[c]: v / total for c, v in sorted(vote.items(), key=lambda x: -x[1])}
                out.append((probs, float(row[top].max())))
        return out

    def nbytes(self) -> int:
        m = self.matrix
        return m.data.nbytes + m.indices.nbytes + m.indptr.nbytes + self.labels.nbytes

    def save(self, path: str | Path) -> None:
        import joblib  # noqa: PLC0415

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"vectorizer": self.vectorizer, "matrix": self.matrix, "labels": self.labels,
                     "codes": self.codes}, path, compress=3)


def load(path: str | Path | None) -> Optional[CategoryModel]:
    """Модель категорий или None, если файла нет (тогда конвейер ищет по всему каталогу, как раньше)."""
    if not path or not Path(path).exists():
        return None
    import joblib  # noqa: PLC0415

    t = time.perf_counter()
    d = joblib.load(path)
    model = CategoryModel(d["vectorizer"], d["matrix"], d["labels"], d["codes"])
    log.info("Модель категорий: %d названий, %d кодов ОКПД2, %.0f МБ, %.1f с", model.matrix.shape[0],
             len(model.codes), model.nbytes() / 2**20, time.perf_counter() - t)
    return model


def read_ste(ste_path: str, okpd_ok: set[str], excluded: set[str], per_code: int,
             seed: int = 11) -> tuple[list[str], list[str]]:
    """Уникальные названия карточек СТЕ с полным ОКПД2 из каталога; не больше per_code названий на код (0 — все)."""
    import pyarrow.parquet as pq  # noqa: PLC0415

    from .ste_eval import FULL_OKPD2  # noqa: PLC0415

    f = pq.ParquetFile(ste_path)
    by_code: dict[str, list[str]] = defaultdict(list)
    seen = set()
    for g in range(f.metadata.num_row_groups):
        t = f.read_row_group(g, columns=["name", "okpd2_code"])
        for name, code in zip(t.column(0).to_pylist(), t.column(1).to_pylist()):
            if not code or not FULL_OKPD2.match(code) or code not in okpd_ok:
                continue
            key = norm_name(name)
            if not key or key in excluded or (key, code) in seen:
                continue
            seen.add((key, code))
            by_code[code].append(key)
    rnd = random.Random(seed)
    names, codes = [], []
    for code in sorted(by_code):
        lst = by_code[code]
        if per_code and len(lst) > per_code:
            lst = rnd.sample(lst, per_code)
        names += lst
        codes += [code] * len(lst)
    return names, codes


def main() -> None:
    from .ste_eval import active_okpd2, load_names  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Модель категорий ОКПД2 по названиям карточек СТЕ")
    ap.add_argument("--ste", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--exclude", nargs="*", default=[], help="проверочные наборы (raw_text) — не брать в обучение")
    ap.add_argument("--per-code", type=int, default=300, help="не больше стольких названий на код ОКПД2 (0 — все)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t = time.perf_counter()
    from . import category as module  # noqa: PLC0415  # не __main__: иначе pickle сошлётся на __main__.NameVectorizer

    names, codes = read_ste(args.ste, active_okpd2(args.catalog), load_names(args.exclude), args.per_code)
    model = module.CategoryModel.fit(names, codes)
    model.save(args.out)
    print(f"Модель категорий: {len(names)} названий, {len(model.codes)} кодов ОКПД2, "
          f"{model.nbytes() / 2**20:.0f} МБ в памяти, {time.perf_counter() - t:.0f} с → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
