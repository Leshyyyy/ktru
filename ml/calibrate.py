"""Калибровка уверенности: признаки кандидата → вероятность, что именно этот код верный.

    python -m ml.calibrate --data data/ktru/eval/dev_synth.jsonl

обучает логистическую регрессию + изотоническую калибровку на dev и сохраняет ml/artifacts/calibrator.joblib.
Пока файла нет, работает эвристика HeuristicCalibrator.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from contracts.ml_contracts import FEATURE_NAMES


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


def fit(rows: list[tuple[dict[str, float], int]]) -> FittedCalibrator:
    from sklearn.isotonic import IsotonicRegression  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.model_selection import cross_val_predict  # noqa: PLC0415

    x = [[f.get(n, 0.0) for n in FEATURE_NAMES] for f, _ in rows]
    y = [label for _, label in rows]
    logreg = LogisticRegression(max_iter=1000, C=1.0)
    # Изотоническая калибровка — на предсказаниях кросс-валидации, иначе она подстроится под переобученные оценки.
    oof = cross_val_predict(logreg, x, y, cv=5, method="predict_proba")[:, 1]
    isotonic = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(oof, y)
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


def main() -> None:
    from contracts.ml_contracts import RawItem  # noqa: PLC0415

    from .config import Settings  # noqa: PLC0415
    from .pipeline import KtruPipeline  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Обучить калибратор уверенности на dev")
    ap.add_argument("--data", required=True, help="JSONL разметки (формат docs/analytics/09-data-quality.md)")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.split != "dev":
        raise SystemExit("Калибратор обучается только на dev (правила честной оценки, раздел 9).")

    settings = Settings()
    samples = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    samples = [s for s in samples if s.get("split") == "dev"]
    pipe = KtruPipeline(settings, calibrator=HeuristicCalibrator())
    items = [RawItem(item_id=s["id"], text=s["raw_text"]) for s in samples]
    ranked = pipe.rank(items, use_feedback=False)

    def rows_for(idx):
        out = []
        for i in idx:
            for cand in ranked[i].candidates[:5]:
                out.append((cand.features, int(cand.ktru_code == samples[i]["expected_ktru_code"])))
        return out

    # Честная проверка: две половины dev, разбитые по кодам КТРУ (тексты одного кода не попадают в обе).
    codes = sorted({s["expected_ktru_code"] for s in samples})
    half = set(codes[::2])
    folds = [[i for i, s in enumerate(samples) if (s["expected_ktru_code"] in half) == flag] for flag in (True, False)]
    for k, (train, test) in enumerate(((folds[0], folds[1]), (folds[1], folds[0])), start=1):
        cal_k = fit(rows_for(train))
        conf, correct = [], []
        for i in test:
            cands = ranked[i].candidates
            if cands:
                best = max(cands[:3], key=lambda c: cal_k.predict(c.features))
                conf.append(cal_k.predict(best.features))
                correct.append(best.ktru_code == samples[i]["expected_ktru_code"])
        print(f"  проверка {k}: ECE={_ece(conf, correct):.3f}, top-1={sum(correct) / len(correct):.3f} на {len(test)} позициях")

    cal = fit(rows_for(range(len(samples))))
    out = args.out or settings.calibrator_path
    cal.save(out)
    print(f"Калибратор обучен на {len(samples)} позициях, сохранён в {out}")


def _ece(conf: list[float], correct: list[bool], bins: int = 10) -> float:
    from .evaluate import ece  # noqa: PLC0415

    return ece(conf, correct, bins)


if __name__ == "__main__":
    main()
