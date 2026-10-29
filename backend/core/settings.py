"""Настройки бэкенда из переменных окружения. Все переменные с пояснениями — в .env.example в корне.

Секреты (пароли) не обязательны на уровне модели: каждому контейнеру передаются только нужные ему,
а отсутствие проверяется там, где значение используется (см. ConfigError).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CATALOG = REPO_ROOT / "contracts" / "fixtures" / "fake_catalog.jsonl"
# Форматы, которые умеет читать backend/files. Какие из них принимает POST /files — UPLOAD_EXTENSIONS.
READABLE_EXTENSIONS = ("xlsx", "xls", "csv", "pdf")


class ConfigError(RuntimeError):
    """Не хватает настройки для запуска компонента. Текст — для человека, без секретов."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    # stub — StubPipeline из contracts/ и каталог-фикстура; real — ml.pipeline и настоящий каталог.
    pipeline: Literal["stub", "real"] = "stub"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # PostgreSQL. api и воркеры работают ролью APP_DB_USER (только чтение и запись данных);
    # владелец схемы POSTGRES_USER нужен только контейнеру migrate.
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "ktru"
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None
    app_db_user: str = "ktru_app"
    app_db_password: SecretStr | None = None
    db_connect_timeout_s: int = Field(3, gt=0)
    api_statement_timeout_ms: int = Field(30_000, gt=0)

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = Field(0, ge=0)
    redis_password: SecretStr | None = None

    upload_dir: Path = Path("/data/uploads")
    # Через сколько часов удалять загруженный файл, по которому так и не создали задачу.
    upload_ttl_hours: int = Field(24, gt=0)
    # Лимит файла POST /catalog/import (JSONL): полный КТРУ больше лимита спецификаций.
    catalog_import_max_mb: int = Field(300, gt=0)
    # Через запятую. xls читается, но по договорённости команды пока не принимается (вопрос в PR #5).
    upload_extensions: Annotated[list[str], NoDecode] = ["xlsx", "csv", "pdf"]
    max_file_mb: int = Field(50, gt=0)
    max_rows: int = Field(5000, gt=0)
    max_items_per_job: int = Field(1000, gt=0)
    classify_max_items: int = Field(20, gt=0)
    # Сколько POST /classify ждёт ответа worker-ml (он может досчитывать пачку большой задачи), секунд.
    classify_timeout_s: float = Field(30, gt=0)
    green_threshold: float = 0.70  # замер ML (#21); в примере контракта 0.85
    yellow_threshold: float = 0.50

    # Через запятую. Нужен только для фронта на отдельном адресе: через nginx фронт и API на одном адресе.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    # Swagger по адресу /api/v1/docs. Источник правды для API — contracts/openapi.yaml, а не он.
    api_docs: bool = False

    # Тот же файл читает ML (ml/config.py): в режиме real migrate загружает в базу ровно его.
    ktru_catalog_path: Path | None = None
    # Разметка для оценки качества (*.jsonl, поля eval_samples): её загружает migrate.
    eval_samples_dir: Path = REPO_ROOT / "contracts" / "fixtures" / "eval"

    ml_ready_ttl_s: int = Field(30, gt=0)
    ml_heartbeat_interval_s: int = Field(10, gt=0)
    # Сколько ждать загрузки моделей в дочернем процессе worker-ml (у Celery по умолчанию 4 с).
    ml_worker_init_timeout_s: int = Field(600, gt=0)

    @field_validator(
        "postgres_user", "postgres_password", "app_db_password", "redis_password", "ktru_catalog_path", mode="before"
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # «LLM_API_KEY=» и подобные пустые строки в .env означают «не задано», а не пустой пароль или путь «.».
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cors_origins", "upload_extensions", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("upload_extensions")
    @classmethod
    def _check_extensions(cls, extensions: list[str]) -> list[str]:
        normalized = [ext.lower().removeprefix(".") for ext in extensions]
        unknown = [ext for ext in normalized if ext not in READABLE_EXTENSIONS]
        if unknown or not normalized:
            readable = ", ".join(READABLE_EXTENSIONS)
            raise ValueError(f"UPLOAD_EXTENSIONS: нужен непустой список из {readable}; неизвестные: {unknown}")
        return list(dict.fromkeys(normalized))

    @field_validator("cors_origins")
    @classmethod
    def _check_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parts = urlsplit(origin)
            if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
                raise ValueError(f"CORS_ORIGINS: «{origin}» — нужен адрес вида http://host:port, без «*» и пути")
        return [origin.rstrip("/") for origin in origins]

    @model_validator(mode="after")
    def _check_thresholds(self) -> Settings:
        if not 0 < self.yellow_threshold < self.green_threshold <= 1:
            raise ValueError("Пороги уверенности: нужно 0 < YELLOW_THRESHOLD < GREEN_THRESHOLD ≤ 1")
        if self.ml_heartbeat_interval_s >= self.ml_ready_ttl_s:
            raise ValueError("ML_HEARTBEAT_INTERVAL_S должен быть меньше ML_READY_TTL_S")
        return self

    def app_database_url(self) -> URL:
        if self.app_db_password is None:
            raise ConfigError("Не задан APP_DB_PASSWORD — пароль роли приложения в PostgreSQL")
        return self._database_url(self.app_db_user, self.app_db_password)

    def owner_database_url(self) -> URL:
        if not self.postgres_user or self.postgres_password is None:
            raise ConfigError("Не заданы POSTGRES_USER и POSTGRES_PASSWORD — владелец схемы нужен для миграций")
        return self._database_url(self.postgres_user, self.postgres_password)

    def _database_url(self, user: str, password: SecretStr) -> URL:
        # URL.create сам экранирует спецсимволы в пароле; при выводе в лог пароль скрыт.
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    def redis_url(self) -> str:
        auth = f":{quote(self.redis_password.get_secret_value(), safe='')}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def catalog_seed_path(self) -> Path:
        """Каталог, который migrate загружает при старте."""
        if self.pipeline == "stub":
            # Заглушка отвечает кодами 99.99.*, и они должны находиться в каталоге задачи.
            return FIXTURE_CATALOG
        if self.ktru_catalog_path is None:
            raise ConfigError("PIPELINE=real: задайте KTRU_CATALOG_PATH — тот же файл читает worker-ml")
        return self.ktru_catalog_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
