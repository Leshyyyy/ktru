from ml.calibrate import fit, usable_samples, without_holdout


def test_samples_with_outdated_codes_are_skipped():
    samples = [{"id": "a", "expected_ktru_code": "17.12.14.110-00000008"},
               {"id": "b", "expected_ktru_code": "32.99.12.120-00000002"}]
    kept, skipped = usable_samples(samples, {"17.12.14.110-00000008"})
    assert [s["id"] for s in kept] == ["a"] and skipped == 1


def test_holdout_codes_and_texts_are_not_used_for_training():
    samples = [{"id": "same_code", "expected_ktru_code": "17.12.14.110-00000008", "raw_text": "Бумага А4"},
               {"id": "same_text", "expected_ktru_code": "32.99.12.120-00000002", "raw_text": "РУЧКА,  синяя"},
               {"id": "other", "expected_ktru_code": "22.29.25.000-00000002", "raw_text": "Папка пластиковая"}]
    holdout = [{"expected_ktru_code": "17.12.14.110-00000008", "raw_text": "Бумага офисная"},
               {"expected_ktru_code": "32.99.12.110-00000005", "raw_text": "Ручка синяя"}]
    kept, skipped = without_holdout(samples, holdout)
    assert [s["id"] for s in kept] == ["other"] and skipped == 2


def test_confidence_scale_follows_top_candidates():
    # У каждой позиции лучший кандидат верен, четыре остальных — нет. Шкала по всем кандидатам дала бы лучшему ~0,2.
    rows = []
    for i in range(40):
        rows.append(({"top1_fused": 0.9 + i * 0.001}, 1, (i, "m")))
        rows += [({"top1_fused": 0.1 + j * 0.05 + i * 0.001}, 0, (i, "m")) for j in range(4)]
    cal = fit(rows)
    assert cal.predict({"top1_fused": 0.92}) > 0.9
