from ml.calibrate import usable_samples


def test_samples_with_outdated_codes_are_skipped():
    samples = [{"id": "a", "expected_ktru_code": "17.12.14.110-00000008"},
               {"id": "b", "expected_ktru_code": "32.99.12.120-00000002"}]
    kept, skipped = usable_samples(samples, {"17.12.14.110-00000008"})
    assert [s["id"] for s in kept] == ["a"] and skipped == 1
