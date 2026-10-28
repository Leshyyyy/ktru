"""Настройки ML из переменных окружения (docs/analytics/07-ml.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_ROOT / "data" / "ktru" / "ktru_data" / "catalog.jsonl"
FIXTURE_CATALOG = REPO_ROOT / "contracts" / "fixtures" / "fake_catalog.jsonl"
ARTIFACTS = REPO_ROOT / "ml" / "artifacts"  # отчёты и кэш, не в git
MODELS = REPO_ROOT / "ml" / "models"  # обученные артефакты (калибратор) — в git и в Docker-образе


def load_dotenv(*paths: Path) -> None:
    """Подхватить KEY=VALUE из .env-файлов. Уже заданные переменные окружения не перезаписываются."""
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ml/.env — локальные ключи разработчика (в .gitignore); корневой .env — общий для docker-compose.
load_dotenv(REPO_ROOT / "ml" / ".env", REPO_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # По умолчанию то, что реально работает без ключей и без torch: правила и сверка характеристик.
    # hybrid/llm-извлечение пока не реализовано, cross_encoder требует sentence-transformers.
    extractor: str = field(default_factory=lambda: _env("EXTRACTOR", "rules"))
    reranker: str = field(default_factory=lambda: _env("RERANKER", "none"))
    device: str = field(default_factory=lambda: _env("DEVICE", "cpu"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    jev_model: str = field(default_factory=lambda: _env("JEV_MODEL", "typesafe/jev-1.14"))
    # Пусто = TF-IDF по символьным n-граммам (работает без скачивания моделей).
    embedder_model: str = field(default_factory=lambda: _env("EMBEDDER_MODEL"))
    embedding_dim: int = field(default_factory=lambda: int(_env("EMBEDDING_DIM", "1024")))
    cross_encoder_model: str = field(default_factory=lambda: _env("CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3"))
    catalog_path: str = field(default_factory=lambda: _env("KTRU_CATALOG_PATH"))
    # Под каким catalog_version_id загрузить файл KTRU_CATALOG_PATH в get_pipeline(); «» — задачи без версии.
    catalog_version_id: str = field(default_factory=lambda: _env("KTRU_CATALOG_VERSION_ID"))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    calibrator_path: str = field(default_factory=lambda: _env("CALIBRATOR_PATH", str(MODELS / "calibrator.joblib")))
    # Модель категорий ОКПД2 по карточкам СТЕ (python -m ml.category) — в git и в образе, как калибратор.
    # Нет файла — поиск по всему каталогу, как до двухступенчатой схемы. «-» — выключить.
    category_model_path: str = field(
        default_factory=lambda: _env("CATEGORY_MODEL_PATH", str(MODELS / "category.joblib")))
    # Кэш эмбеддингов каталога; в docker-compose — том /data/models (переживает перезапуск воркера).
    embed_cache_dir: str = field(default_factory=lambda: _env("EMBED_CACHE_DIR", str(ARTIFACTS / "embeddings")))
    llm_cache_path: str = field(default_factory=lambda: _env("LLM_CACHE_PATH", str(ARTIFACTS / "llm_cache.jsonl")))
    # Доля оценки реранкера в итоговом балле на пути rerank. Малая: Jev разрешает ничьи, а не перекрывает сверку
    # характеристик (на dev вес 0.25–0.75 исправлял и портил поровну, см. ml/CLAUDE.md).
    rerank_weight: float = field(default_factory=lambda: float(_env("RERANK_WEIGHT", "0.05")))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and (self.llm_base_url or self.reranker == "llm"))

    def resolved_catalog_path(self) -> Path:
        if self.catalog_path:
            return Path(self.catalog_path)
        return DEFAULT_CATALOG if DEFAULT_CATALOG.exists() else FIXTURE_CATALOG
