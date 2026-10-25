"""Настройки ML из переменных окружения (docs/analytics/07-ml.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_ROOT / "data" / "ktru" / "ktru_data" / "catalog.jsonl"
FIXTURE_CATALOG = REPO_ROOT / "contracts" / "fixtures" / "fake_catalog.jsonl"
ARTIFACTS = REPO_ROOT / "ml" / "artifacts"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    extractor: str = field(default_factory=lambda: _env("EXTRACTOR", "hybrid"))
    reranker: str = field(default_factory=lambda: _env("RERANKER", "cross_encoder"))
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
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    calibrator_path: str = field(default_factory=lambda: _env("CALIBRATOR_PATH", str(ARTIFACTS / "calibrator.joblib")))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and (self.llm_base_url or self.reranker == "llm"))

    def resolved_catalog_path(self) -> Path:
        if self.catalog_path:
            return Path(self.catalog_path)
        return DEFAULT_CATALOG if DEFAULT_CATALOG.exists() else FIXTURE_CATALOG
