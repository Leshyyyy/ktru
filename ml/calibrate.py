"""Калибровка уверенности: признаки кандидата → вероятность, что именно этот код верный.

    python -m ml.calibrate --data data/ktru/v2/eval_samples_v3_dev.jsonl data/ktru/ste/ste_dev.jsonl

обучает логистическую регрессию + изотоническую калибровку на dev и сохраняет ml/models/calibrator.joblib.
Пока файла нет, работает эвристика HeuristicCalibrator.

Наборы из СТЕ (эталон «<ОКПД2>-X», ml.ste_eval) знают только категорию. Из них берутся позиции, у ОКПД2 которых
в каталоге одна позиция для поиска: там верный ОКПД2 означает верный код. Без живых строк калибратор,
обученный на одной синтетике, завышает уверенность на настоящих заявках.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from contracts.ml_contracts import FEATURE_NAMES

from .category import norm_name


class HeuristicCalibrator:
    fitted = False

    def predict(self, features: dict[str, float]) -> float:
        z = (
            -4.0
            + 4.0 * features.get("top1_fused", 0.0)
            + 4.0 * features.get("margin_top1_top2", 0.0)
            + 1.5 * features.get("char_match_ratio", 0.0)
            + 2.0 * features.get("confirmed_vote", 0.0)
        )
        return 1.0 / (1.0 + math.exp(-z))


class FittedCalibrator:
    fitted = True

    def __init__(self, logreg, isotonic):
        self.logreg = logreg
        self.isotonic = isotonic

    def predict(self, features: dict[str, float]) -> float:
        x = [[features.get(n, 0.0) for n in FEATURE_NAMES]]
        p = self.logreg.predict_proba(x)[0, 1]
        return float(self.isotonic.predict([p])[0])

    def save(self, path: str | Path) -> None:
        import joblib  # noqa: PLC0415

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"logreg": self.logreg, "isotonic": self.isotonic, "features": FEATURE_NAMES}, path)


def fit(rows: list[tuple[dict[str, float], int, tuple[int, str]]]) -> FittedCalibrator:
    """rows — (признаки кандидата, верен ли он, (номер позиции, режим конвейера)).

    Логистическая регрессия учится на всех кандидатах, а шкала уверенности — только на лучшем кандидате каждой позиции:
    пользователю показывают именно его, а среди всех кандидатов большинство заведомо неверные (ECE первого кандидата
    на отложенной части v3.2 dev — 0,031 против 0,045 при шкале по всем кандидатам)."""
    from sklearn.isotonic import IsotonicRegression  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.model_selection import GroupKFold, cross_val_predict  # noqa: PLC0415

    x = [[f.get(n, 0.0) for n in FEATURE_NAMES] for f, _, _ in rows]
    y = [label for _, label, _ in rows]
    logreg = LogisticRegression(max_iter=1000, C=1.0)
    # Изотоническая калибровка — на предсказаниях кросс-валидации, иначе она подстроится под переобученные оценки.
    # Кандидаты одной позиции (в обоих режимах) — в одной части разбиения.
    oof = cross_val_predict(logreg, x, y, cv=GroupKFold(5), groups=[query[0] for _, _, query in rows],
                            method="predict_proba")[:, 1]
    best: dict[tuple[int, str], int] = {}
    for k, (_, _, query) in enumerate(rows):
        if query not in best or oof[k] > oof[best[query]]:
            best[query] = k
    top = sorted(best.values())
    isotonic = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof[top], [y[k] for k in top])
    logreg.fit(x, y)
    return FittedCalibrator(logreg, isotonic)


def load(path: str | Path):
    p = Path(path)
    if not p.exists():
        return HeuristicCalibrator()
    import joblib  # noqa: PLC0415

    d = joblib.load(p)
    if tuple(d.get("features", ())) != tuple(FEATURE_NAMES):
        return HeuristicCalibrator()
    return FittedCalibrator(d["logreg"], d["isotonic"])


def usable_samples(samples: list[dict], active_codes: set[str]) -> tuple[list[dict], int]:
    """Примеры, чей код — действующая позиция каталога. Разметка, собранная по старому каталогу, может ссылаться на
    код, ставший архивным: такой ответ система не выдаст, и калибратор учился бы на заведомых ошибках."""
    kept = [s for s in samples if s["expected_ktru_code"] in active_codes]
    return kept, len(samples) - len(kept)


def without_holdout(samples: list[dict], holdout: list[dict]) -> tuple[list[dict], int]:
    """Примеры без кодов и текстов контрольного набора. Разметка разных версий пересекается: строка старого dev может
    оказаться в новом test, и тогда контрольный замер уверенности будет лучше настоящего."""
    codes = {s["expected_ktru_code"] for s in holdout}
    texts = {norm_name(s["raw_text"]) for s in holdout}
    kept = [s for s in samples if s["expected_ktru_code"] not in codes and norm_name(s["raw_text"]) not in texts]
    return kept, len(samples) - len(kept)


def _read_jsonl(paths: list[str]) -> list[dict]:
    return [json.loads(line) for path in paths for line in open(path, encoding="utf-8") if line.strip()]


def main() -> None:
    from contracts.ml_contracts import RawItem  # noqa: PLC0415

    from .config import Settings  # noqa: PLC0415
    from .pipeline import KtruPipeline  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Обучить калибратор уверенности на dev")
    ap.add_argument("--data", required=True, nargs="+",
                    help="JSONL разметки (формат docs/analytics/09-data-quality.md); можно несколько")
    ap.add_argument("--holdout", nargs="*", default=[],
                    help="контрольные наборы (test): их коды и тексты не попадают в обучение")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.split != "dev":
        raise SystemExit("Калибратор обучается только на dev (правила честной оценки, раздел 9).")

    settings = Settings()
    samples = [s for s in _read_jsonl(args.data) if s.get("split") == "dev"]
    pipe = KtruPipeline(settings, calibrator=HeuristicCalibrator())
    per_okpd = Counter(e.okpd2_code for e in pipe.retriever.items)
    single = {e.okpd2_code: e.ktru_code for e in pipe.retriever.items if per_okpd[e.okpd2_code] == 1}
    for s in samples:
        gold = s["expected_ktru_code"]
        if gold.endswith("-X"):
            s["expected_ktru_code"] = single.get(gold[:-2], "")
    skipped = sum(not s["expected_ktru_code"] for s in samples)
    samples = [s for s in samples if s["expected_ktru_code"]]
    if skipped:
        print(f"Позиций СТЕ без однозначного кода (в ОКПД2 несколько позиций КТРУ) пропущено: {skipped}")
    samples, outdated = usable_samples(samples, {e.ktru_code for e in pipe.retriever.items})
    if outdated:
        print(f"Позиций с кодом, которого нет среди действующих позиций каталога (старая разметка), пропущено: {outdated}")
    samples, held = without_holdout(samples, _read_jsonl(args.holdout))
    if held:
        print(f"Позиций с кодом или текстом из контрольного набора пропущено: {held}")
    items = [RawItem(item_id=s["id"], text=s["raw_text"]) for s in samples]
    # Один калибратор на оба режима: воркер работает без эмбеддингов, если модель не загрузилась, а признаки кандидатов
    # там другие. Обученный только с эмбеддингами калибратор в этом режиме выходит за ECE 0,05 (замеры — ml/CLAUDE.md).
    modes = {"с эмбеддингами": pipe.rank(items, use_feedback=False)}
    if pipe.retriever.embedder is not None:
        plain = KtruPipeline(settings, catalog=pipe.catalog, calibrator=HeuristicCalibrator(), embedder=None)
        modes["без эмбеддингов"] = plain.rank(items, use_feedback=False)

    def rows_for(idx):
        return [(cand.features, int(cand.ktru_code == samples[i]["expected_ktru_code"]), (i, mode))
                for mode, ranked in modes.items() for i in idx for cand in ranked[i].candidates[:5]]

    # Честная проверка: две половины dev, разбитые по кодам КТРУ (тексты одного кода не попадают в обе).
    codes = sorted({s["expected_ktru_code"] for s in samples})
    half = set(codes[::2])
    folds = [[i for i, s in enumerate(samples) if (s["expected_ktru_code"] in half) == flag] for flag in (True, False)]
    for k, (train, test) in enumerate(((folds[0], folds[1]), (folds[1], folds[0])), start=1):
        cal_k = fit(rows_for(train))
        for mode, ranked in modes.items():
            conf, correct = [], []
            for i in test:
                cands = ranked[i].candidates
                if cands:
                    best = max(cands[:3], key=lambda c: cal_k.predict(c.features))
                    conf.append(cal_k.predict(best.features))
                    correct.append(best.ktru_code == samples[i]["expected_ktru_code"])
            print(f"  проверка {k}, {mode}: ECE={_ece(conf, correct):.3f}, top-1={sum(correct) / len(correct):.3f} "
                  f"на {len(test)} позициях")

    cal = fit(rows_for(range(len(samples))))
    out = args.out or settings.calibrator_path
    cal.save(out)
    print(f"Калибратор обучен на {len(samples)} позициях, сохранён в {out}")


def _ece(conf: list[float], correct: list[bool], bins: int = 10) -> float:
    from .evaluate import ece  # noqa: PLC0415

    return ece(conf, correct, bins)


if __name__ == "__main__":
    main()
