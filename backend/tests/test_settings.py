from __future__ import annotations

import re

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from backend.core.settings import FIXTURE_CATALOG, REPO_ROOT, ConfigError, Settings


def test_defaults_match_contract_example() -> None:
    s = Settings()
    assert (s.max_file_mb, s.max_rows, s.max_items_per_job, s.classify_max_items) == (50, 5000, 1000, 20)
    assert (s.green_threshold, s.yellow_threshold) == (0.70, 0.5)  # зелёный порог — по замеру ML, не из примера
    assert s.pipeline == "stub"


@pytest.mark.parametrize(("green", "yellow"), [(0.5, 0.85), (0.85, 0.85), (1.2, 0.5), (0.85, 0.0)])
def test_thresholds_must_be_ordered(green: float, yellow: float) -> None:
    with pytest.raises(ValidationError, match="Пороги уверенности"):
        Settings(green_threshold=green, yellow_threshold=yellow)


def test_cors_origins_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, https://ktru.example/ ,")
    assert Settings().cors_origins == ["http://localhost:5173", "https://ktru.example"]


@pytest.mark.parametrize("origin", ["*", "localhost:5173", "http://host/path", "ftp://host"])
def test_cors_origins_reject_wildcards_and_paths(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    monkeypatch.setenv("CORS_ORIGINS", origin)
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings()


def test_database_url_escapes_password_and_hides_it() -> None:
    s = Settings(app_db_user="app", app_db_password="p@ss:w/rd#1")
    url = s.app_database_url()
    assert make_url(url.render_as_string(hide_password=False)).password == "p@ss:w/rd#1"
    assert "p@ss" not in repr(url)
    assert "p@ss" not in str(url)


def test_missing_secrets_raise_config_error_without_leaking() -> None:
    with pytest.raises(ConfigError, match="APP_DB_PASSWORD"):
        Settings().app_database_url()
    with pytest.raises(ConfigError, match="POSTGRES_USER"):
        Settings(postgres_user="owner").owner_database_url()


def test_redis_url_escapes_password() -> None:
    assert Settings(redis_password="a/b:c@d").redis_url() == "redis://:a%2Fb%3Ac%40d@redis:6379/0"
    assert Settings().redis_url() == "redis://redis:6379/0"


def test_catalog_seed_path() -> None:
    assert Settings(pipeline="stub", ktru_catalog_path="/srv/data/catalog.jsonl").catalog_seed_path() == FIXTURE_CATALOG
    assert (
        str(Settings(pipeline="real", ktru_catalog_path="/x/catalog.jsonl").catalog_seed_path()) == "/x/catalog.jsonl"
    )
    with pytest.raises(ConfigError, match="KTRU_CATALOG_PATH"):
        Settings(pipeline="real").catalog_seed_path()


def test_env_example_lists_every_setting() -> None:
    """Новая настройка без строки в .env.example — проверяющий о ней не узнает."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))
    missing = {name.upper() for name in Settings.model_fields} - documented
    assert not missing, f"Нет в .env.example: {sorted(missing)}"
